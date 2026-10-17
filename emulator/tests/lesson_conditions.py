"""#854 — a lesson's conditions, per step: the grammar and a matcher, in Python.

`app/ios/Lessons/README.md` is the grammar's one home; this is its reader for the
emulator's tests, the twin of the app's `Model/Learn.swift` (`Lesson.parse`,
`Requirement`, `Guide.feed`), so `test_lessons.py` can check, without Xcode,
that each step of each lesson turns green at the moment its script says and not
before. The app's matcher for steps is #855's (`Guide.feed`, `Guide.look`,
`Block`); change the two together, and `LearnTests.testEachStepTurnsGreenAtItsMarkedScriptStep`
replays the same record through the Swift.

A step's lines, indented two spaces under it:

    Press [TRACK], then [1].  -> TRACK 1
      done: key 1 on TRACK
      then: is transport.selected = 1
    The top line says where you are.  -> header
      manual

An `is` line reads the situation document (#853, `audio/host/live-schema.json`):
`is <path>` (true, non-zero, non-empty) or `is <path> <op> <value>`, op one of
`= != < <= > >=`, the value a number, `true`, `false` or a word; tests joined by
`and` must hold at one moment. On a list (`keys.held`) `=` means "not empty,
and every item matches" (`*` ends a pattern, as in ids).
"""

import re

OPS = ("<=", ">=", "!=", "=", "<", ">")
KINDS = ("key", "turn", "page", "rec", "app")


class LessonError(ValueError):
    pass


class Cond:
    """One `done:` / `then:` line (Swift's `Requirement`, plus `is`)."""

    def __init__(self, kind):
        self.kind = kind
        self.count = 1
        self.distinct = False
        self.ids = []
        self.params = []
        self.shift = False
        self.holding = None
        self.context = []
        self.ordered = False
        self.tests = []            # `is` only: [(["tracks", "1", "has_clip"], op, value)]

    # -- the grammar --------------------------------------------------------

    @staticmethod
    def parse(s):
        words = s.split()
        if words[:1] == ["is"]:
            return Cond._parse_is(words[1:])
        # `rec on` is the kind's own word, after any count: `3 rec on` is three takes.
        kind_word = next((w for w in words if not _is_int(w) and w != "distinct"), None)
        context = []
        if "on" in words and words.index("on") > 0 and kind_word != "rec":
            on = words.index("on")
            context = [p.strip() for p in re.split("[›>]", " ".join(words[on + 1:])) if p.strip()]
            if not context:
                raise LessonError("`on` wants a page")
            words = words[:on]
        c = None
        count = 1
        if words and _is_int(words[0]):
            count = int(words.pop(0))
        distinct = False
        if words[:1] == ["distinct"]:
            distinct = True
            words.pop(0)
        if not words or words[0] not in KINDS:
            raise LessonError("done: wants key, turn, page, rec, app or is")
        c = Cond(words.pop(0))
        c.count, c.distinct, c.context = count, distinct, context
        if "with" in words:
            w = words.index("with")
            if len(words) != w + 2:
                raise LessonError("`with` takes one key")
            if words[w + 1] == "SHIFT":
                c.shift = True
            else:
                c.holding = words[w + 1]
            words = words[:w]
        if c.kind in ("key", "page", "app"):
            if len(words) != 1:
                raise LessonError("%s wants one list of ids" % c.kind)
            c.ids = words[0].split("|")
        elif c.kind == "turn":
            if "param" in words:
                p = words.index("param")
                if len(words) != p + 2:
                    raise LessonError("`param` wants names")
                c.params = words[p + 1].split("|")
                words = words[:p]
            if len(words) > 1:
                raise LessonError("turn wants one list of encoders")
            c.ids = words[0].split("|") if words else []
        else:
            if words not in (["on"], ["off"]):
                raise LessonError("rec on or rec off")
            c.ids = words
        return c

    @staticmethod
    def _parse_is(words):
        c = Cond("is")
        test = []
        for w in words + ["and"]:
            if w != "and":
                test.append(w)
                continue
            if len(test) not in (1, 3) or (len(test) == 3 and test[1] not in OPS):
                raise LessonError("is <field> [<op> <value>] [and …], op one of %s" % " ".join(OPS))
            op = value = None
            if len(test) == 3:
                op, v = test[1], test[2]
                value = {"true": True, "false": False}.get(v, _number(v) if _number(v) is not None else v)
            c.tests.append((test[0].split("."), op, value))
            test = []
        return c

    # -- the matcher ----------------------------------------------------------

    def matches(self, action, page, header, held):
        """Whether `action` counts toward this (Swift's `Requirement.matches`)."""
        k = action.get("kind")
        if self.kind == "key":
            if k != "key" or action.get("down") is not True or not _any(self.ids, action.get("id", "")):
                return False
            if self.shift and "SHIFT" not in held:
                return False
            if self.holding and self.holding not in held:
                return False
        elif self.kind == "turn":
            if k != "encoder":
                return False
            if self.ids and not _any(self.ids, action.get("id") or ""):
                return False
            if self.params and not _any(self.params, action.get("param") or ""):
                return False
            if self.shift and action.get("shift") is not True:
                return False
            if self.holding and self.holding not in held:
                return False
        elif self.kind == "page":
            return k == "page" and _any(self.ids, action.get("page") or "")
        elif self.kind == "rec":
            return k == "rec" and action.get("on") is (self.ids[0] == "on")
        elif self.kind == "app":
            return k == "app" and _any(self.ids, action.get("id") or "")
        else:
            return False                     # `is` reads the situation, not actions
        return self._in_context(page, header)

    def holds(self, doc):
        """`is`: whether the situation document says so now (every test of the line)."""
        return all(_holds(doc, path, op, want) for path, op, want in self.tests)

    def distinct_value(self, action):
        return (action.get("param") or action.get("id")) if self.kind == "turn" else action.get("id")

    def _in_context(self, page, header):
        if not self.context:
            return True
        if header is not None:
            parts = [p.strip() for p in header.split("›")]
            if len(parts) >= len(self.context) and all(
                    a.lower() == b.lower() for a, b in zip(parts, self.context)):
                return True
        # No map yet, or the header names the page differently (FILE is `USB/FILE`).
        return len(self.context) == 1 and page is not None and page.lower() == self.context[0].lower()


def _holds(doc, path, op, want):
    v = doc
    for p in path:
        if not isinstance(v, dict) or p not in v:
            return False                     # an absent field is never met
        v = v[p]
    if op is None:
        return bool(v)
    if isinstance(v, list):
        # a list (`keys.held`): `=` is "not empty, and every item matches"
        if op not in ("=", "!="):
            return False
        every = bool(v) and all(_any([str(want)], str(x)) for x in v)
        return every == (op == "=")
    if isinstance(want, bool) or isinstance(v, bool):
        if op not in ("=", "!="):
            return False
        return (v is want) == (op == "=")
    if isinstance(want, (int, float)) and isinstance(v, (int, float)):
        return {"=": v == want, "!=": v != want, "<": v < want, "<=": v <= want,
                ">": v > want, ">=": v >= want}[op]
    if op in ("=", "!="):
        return (str(v).lower() == str(want).lower()) == (op == "=")
    return False


def _is_int(w):
    return re.fullmatch(r"[+-]?\d+", w) is not None


def _number(v):
    try:
        return int(v) if _is_int(v) else float(v)
    except ValueError:
        return None


def _any(patterns, value):
    for p in patterns:
        if p.endswith("*"):
            if value.upper().startswith(p[:-1].upper()):
                return True
        elif value.lower() == p.lower():
            return True
    return False


class Step:
    def __init__(self, text, targets):
        self.text = text
        self.targets = targets
        self.conds = []
        self.manual = False


class Lesson:
    def __init__(self, lid):
        self.id = lid
        self.title = ""
        self.steps = []
        self.done = []                       # the lesson's own block, unindented


def parse(text, lid):
    """One `.lesson` file, as `Lesson.parse` reads it, with the steps' own lines."""
    lesson = Lesson(lid)
    in_header = True
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()

        def fail(m):
            return LessonError("%s.lesson line %d: %s" % (lid, n, m))
        if line.startswith("//"):
            continue
        if not line:
            if lesson.title:
                in_header = False
            continue
        if line.startswith("# "):
            if lesson.title:
                raise fail("a second title")
            lesson.title = line[2:].strip()
            continue
        indented = raw[:1] in (" ", "\t")
        if indented and (line == "manual" or line[:5] in ("done:", "then:")):
            if not raw.startswith("  ") or raw.startswith("   "):
                raise fail("a step's own line is indented two spaces")
            if not lesson.steps:
                raise fail("a step's own line comes under a step")
            step = lesson.steps[-1]
            if line == "manual":
                if step.conds or step.manual:
                    raise fail("`manual` is a step's only line")
                step.manual = True
                continue
            if step.manual:
                raise fail("a `manual` step has no other line")
            block = step.conds
        elif line[:5] in ("done:", "then:"):
            block = lesson.done
        else:
            block = None
        if block is not None:
            ordered = line.startswith("then:")
            if ordered and not block:
                raise fail("`then:` follows a `done:` line")
            try:
                c = Cond.parse(line[5:])
            except LessonError as e:
                raise fail(str(e))
            c.ordered = ordered
            block.append(c)
            continue
        if in_header and ":" in line:
            continue                         # the header: `test_lessons` reads none of it
        if not lesson.title:
            raise fail("the first line is the title, `# …`")
        in_header = False
        if "->" in line:
            text_, targets = line.split("->", 1)
            lesson.steps.append(Step(text_.strip(), targets.split()))
        else:
            lesson.steps.append(Step(line, []))
    return lesson


class Block:
    """One block of lines being counted (Swift's `Guide.feed`, for one lesson)."""

    def __init__(self, conds):
        self.conds = conds
        self.counts = [0] * len(conds)
        self.seen = [set() for _ in conds]

    def met(self, counts=None):
        return all(c.count <= n for c, n in zip(self.conds, counts or self.counts))

    def _above_met(self, i, counts):
        # A `then:` line waits for every line above it, met before this.
        return all(c.count <= n for c, n in zip(self.conds[:i], counts[:i]))

    def feed(self, action, page, header, held):
        before = list(self.counts)
        for i, c in enumerate(self.conds):
            if not c.matches(action, page, header, held):
                continue
            if c.ordered and not self._above_met(i, before):
                continue
            if c.distinct:
                self.seen[i].add(c.distinct_value(action))
                self.counts[i] = len(self.seen[i])
            else:
                self.counts[i] += 1

    def look(self, doc):
        """The situation now: an `is` line, once true, stays met."""
        moved = False
        for i, c in enumerate(self.conds):
            if c.kind == "is" and self.counts[i] == 0 and c.holds(doc) \
                    and (not c.ordered or self._above_met(i, self.counts)):
                self.counts[i] = 1
                moved = True
        return moved


def replay(lesson, events, rows):
    """-> ({step index: the script step it turned green at}, the script step the
    lesson's own block turned green at, or None).

    `events` is a lesson's run (`lesson-runs.json`); `rows` its situations
    (`lesson-situations.json`): per script step, how many events it ended at and
    the whole document then. A step's lines count from the moment every step
    before it that is not `manual` is met (the first step: from the start), so a
    step's press is not met by the same press for an earlier step.
    """
    steps = lesson.steps
    blocks = [None] * len(steps)
    met_at = {}
    whole = Block(lesson.done)
    whole_at = None
    held = set()
    doc = {}

    def open_and_look(i, look=True):
        # `look`: the situation is the one after this script step (never the
        # stale one before it, mid-step)
        while True:
            moved = False
            for k, st in enumerate(steps):
                if st.manual or k in met_at:
                    continue
                if blocks[k] is None:
                    if any(not s.manual and j not in met_at for j, s in enumerate(steps[:k])):
                        break
                    blocks[k] = Block(st.conds)
                    moved = True
                if look and blocks[k].look(doc):
                    moved = True
                if blocks[k].met():
                    met_at[k] = i
                    moved = True
            if not moved:
                return

    open_and_look(0, look=False)
    done = 0
    for i, row in enumerate(rows):
        for e in events[done:row["events"]]:
            a = e["action"]
            for k, b in enumerate(blocks):
                if b is not None and k not in met_at:
                    b.feed(a, e.get("page"), e.get("header"), held)
                    if b.met():
                        met_at[k] = i
            whole.feed(a, e.get("page"), e.get("header"), held)
            if whole_at is None and lesson.done and whole.met():
                whole_at = i
            if a.get("kind") == "key" and a.get("id"):
                (held.add if a.get("down") else held.discard)(a["id"])
            open_and_look(i, look=False)
        done = row["events"]
        doc = row["situation"]
        whole.look(doc)
        if whole_at is None and lesson.done and whole.met():
            whole_at = i
        open_and_look(i)
    return met_at, whole_at
