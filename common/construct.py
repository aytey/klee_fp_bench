"""Build a value of an arbitrary type, by looking for something that yields one.

A library's scalar arguments can be made symbolic directly. Its objects cannot:
an `N_Vector` or a `fftw_plan` is opaque, and making its bytes symbolic would
put a symbolic pointer in KLEE's hands and stop the run on the first
dereference. They have to be *built*, by calling whatever the library offers
for building them.

Which is discoverable. A function yields a T if it returns one, or if it is
named like a constructor and takes a `T *` to write through:

    N_Vector N_VNew_Serial(sunindextype len, SUNContext ctx);
    SUNErrCode SUNContext_Create(SUNComm comm, SUNContext *out);
    void bli_obj_create(num_t dt, dim_t m, dim_t n, inc_t rs, inc_t cs,
                        obj_t *obj);

and the search is recursive, because a constructor's own arguments can be
opaque in turn -- an N_Vector needs a SUNContext, which needs building first.
So this is a search over "what can I make, from what I can already make",
bounded by depth and by a visiting set so a type that needs itself does not
spin.

Two things it cannot infer, and does not pretend to:

* **Relationships between arguments.** That `rs` and `cs` are strides which
  have to agree with `m` and `n` is a precondition of BLIS, not a fact about
  its types. Drivers that violate one show up as an error rather than as
  coverage, which the harness records per driver -- so they are visible, not
  silent.
* **Which integer is a length.** Same reason. Sizes are concrete where an
  object is being built, so that filling it afterwards cannot overrun.

Enums are the pleasant surprise: clang knows their enumerators, so a `num_t`
becomes a symbolic value constrained to the values that type actually has,
rather than an arbitrary int that means nothing to the library.
"""
import re

MAX_DEPTH = 3

# Never built, whatever the search finds: these reach the outside world, and a
# symbolic one is either meaningless or fatal.
DENY = re.compile(r"\b(FILE|va_list|jmp_buf|pthread_|MPI_|DIR)\b")


def normalise(t):
    t = re.sub(r"\bconst\b", "", t).strip()
    t = re.sub(r"\bstruct\s+", "", t)
    return re.sub(r"\s+", " ", t)


def is_pointer_to(t, target):
    """Is `t` spelled `target *`?"""
    t, target = normalise(t), normalise(target)
    return t.endswith("*") and normalise(t[:-1]) == target


class Constructor:
    """Everything the generator knows about how to make values."""

    def __init__(self, decls, scalars, enums, array_maker, size_n,
                 prefixes=(), pins=None):
        self.scalars = scalars          # type -> maker(name) -> (lines, expr)
        self.enums = enums              # type -> [enumerator names]
        self.array_maker = array_maker  # (elem_type, name) -> (lines, expr)
        self.size_n = size_n
        # Only the library under test may supply a constructor. Everything its
        # headers drag in is visible too, and without this the search finds
        # realloc() as a way to make a void and __ctype_get_mb_cur_max() as a
        # way to make a size_t -- both of which it did.
        self.prefixes = tuple(prefixes)
        # Arguments pinned by name rather than explored -- see gen-drivers.
        self.pins = pins or {}

        self.yielders = {}              # type -> [(fn, params, out_index|None)]
        self.accessors = {}             # type -> (fn, element type)
        self._index(decls)

    # -- indexing ---------------------------------------------------------
    def _index(self, decls):
        ctor = re.compile(r"(create|new|alloc|make|init|clone)", re.I)
        for name, ret, params_named in decls:
            params = [p for p, _ in params_named]
            if self.prefixes and not name.startswith(self.prefixes):
                continue
            ret_n = normalise(ret).split("(")[0].strip()

            # Returns the thing.
            if ret_n and ret_n != "void" and ret_n not in self.scalars:
                    self.yielders.setdefault(ret_n, []).append(
                    (name, params_named, None))

            # Writes the thing through an out-parameter. Only for something
            # named like a constructor: plenty of functions take a T* to read.
            if ctor.search(name):
                for i, p in enumerate(params):
                    pn = normalise(p)
                    if pn.endswith("*"):
                        base = normalise(pn[:-1])
                        if base and base not in self.scalars and "*" not in base:
                            self.yielders.setdefault(base, []).append(
                                (name, params_named, i))

            # Hands back the storage inside the thing, so its contents can be
            # made symbolic after it is built.
            if len(params) == 1 and ret_n.endswith("*"):
                elem = normalise(ret_n[:-1])
                if elem in self.scalars and re.search(r"(getarray|data|buffer|ptr)",
                                                      name, re.I):
                    self.accessors.setdefault(normalise(params[0]), (name, elem))

        # Prefer the simplest way to make a thing -- but not the emptiest.
        # "Fewest arguments" alone picks N_VNewEmpty(ctx) over
        # N_VNew_Serial(len, ctx), and an empty vector has no storage: the
        # accessor hands back NULL, filling it faults, and every operation on
        # it is undefined. A constructor that names itself empty, or that
        # clones something there is nothing to clone from, is a shell to be
        # filled in by a caller who knows what goes in it -- which the
        # generator does not.
        hollow = re.compile(r"(empty|null|shell|clone|wrap)", re.I)
        for t, ys in self.yielders.items():
            ys.sort(key=lambda y: (1 if hollow.search(y[0]) else 0,
                                   len(y[1]),
                                   0 if all(normalise(p) in self.scalars
                                            for p, _ in y[1]) else 1,
                                   len(y[0])))

    # -- building ---------------------------------------------------------
    def build(self, ctype, name, depth=0, visiting=None, concrete_sizes=False):
        """(lines, expression) that yield a value of ctype, or None."""
        key = normalise(ctype)
        visiting = visiting or set()

        if DENY.search(key):
            return None, key

        # void is not a value. It reached here because something returning
        # void * was taken for a way to make one.
        if key in ("void", "void *", "void **"):
            return None, key

        if key in self.scalars:
            lines, expr = self.scalars[key](name)
            if concrete_sizes and key in ("int", "unsigned int", "long",
                                          "unsigned long", "size_t"):
                # Building an object: its size has to be the size that will be
                # filled, not a symbolic value near it.
                return ["  %s %s = %d;" % (key, name, self.size_n)], name
            return lines, expr

        if key in self.enums:
            return self._build_enum(key, name), name

        if key.endswith("*"):
            elem = normalise(key[:-1])
            # An array of something scalar.
            if elem in self.scalars:
                return self.array_maker(elem, name)
            # Otherwise the callee wants to be handed one of these to work on.
            # Build the value and pass its address: a library that takes `obj_t
            # *` and one that returns `obj_t` are asking for the same thing,
            # and only one of the two spellings will have a constructor.
            if elem and "*" not in elem and elem not in visiting:
                lines, expr = self.build(elem, name, depth, visiting,
                                         concrete_sizes)
                if lines is not None:
                    return lines, "&" + expr

        if depth >= MAX_DEPTH or key in visiting:
            return None, key

        for fn, params, out_i in self.yielders.get(key, []):
            attempt = self._try(fn, params, out_i, key, name,
                                depth, visiting | {key})
            if attempt is not None:
                return attempt
        return None, key

    def _try(self, fn, params, out_i, key, name, depth, visiting):
        lines, args = [], []
        for i, (p, pname) in enumerate(params):
            pin = self.pins.get(pname)
            if pin is not None and i != out_i:
                lines.append("  %s %s_%d = %s;" % (normalise(p), name, i, pin))
                args.append("%s_%d" % (name, i))
                continue
            if i == out_i:
                args.append("&" + name)
                continue
            sub, expr = self.build(p, "%s_%d" % (name, i), depth + 1,
                                   visiting, concrete_sizes=True)
            if sub is None:
                return None
            lines += sub
            args.append(expr)

        if out_i is None:
            lines = ["  %s %s = %s(%s);" % (key, name, fn, ", ".join(args))] \
                if not lines else lines + \
                ["  %s %s = %s(%s);" % (key, name, fn, ", ".join(args))]
        else:
            lines = [("  %s %s;" % (key, name))] + lines + \
                    ["  %s(%s);" % (fn, ", ".join(args))]

        lines += self._fill(key, name)
        return lines, name

    def _fill(self, key, name):
        """Make the built object's storage symbolic, if it will hand it over.

        Without this an object built by a constructor holds whatever the
        constructor left there -- usually nothing symbolic at all, which would
        make the driver a concrete one wearing a symbolic hat.
        """
        acc = self.accessors.get(key)
        if not acc:
            return []
        fn, elem = acc
        return ["  {",
                "    %s *p_ = %s(%s);" % (elem, fn, name),
                '    klee_make_symbolic(p_, %d * sizeof(%s), "%s_d");'
                % (self.size_n, elem, name),
                "  }"]

    def _build_enum(self, key, name):
        """Symbolic, but only over the values the enum actually has."""
        vals = self.enums[key][:8]
        cond = " || ".join("%s == %s" % (name, v) for v in vals)
        return ["  %s %s;" % (key, name),
                '  klee_make_symbolic(&%s, sizeof(%s), "%s");' % (name, name, name),
                "  klee_assume(%s);" % cond]
