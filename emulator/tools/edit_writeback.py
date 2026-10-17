#!/usr/bin/env python3
"""Write live palette/font edits back into ui/palette.py and ui/font.py.

Only entries that actually changed are rewritten, line by line, so the rest
of each file — comments, ordering, the authored row widths — is untouched.
Keys are parsed with ast.literal_eval rather than reconstructed, so oddballs
like '"' and '\\\\' match their source line exactly.
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # emulator/
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ui import font, palette                                   # noqa: E402

PALETTE_PY = os.path.join(HERE, "ui", "palette.py")
FONT_PY = os.path.join(HERE, "ui", "font.py")


def _split_key(line):
    """'    "A": rest,' -> ('A', indent, rest) or None."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    head, sep, rest = stripped.partition(": ")
    if not sep:
        return None
    try:
        key = ast.literal_eval(head)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(key, str):
        return None
    return key, line[:len(line) - len(stripped)], head


def _block(lines, start_marker):
    """[first, last) line indices of the dict literal opened by start_marker."""
    for i, line in enumerate(lines):
        if line.startswith(start_marker):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("}"):
                    return i + 1, j
            break
    raise LookupError(start_marker)


# --- palette --------------------------------------------------------------

def write_palette(entries=None):
    """Rewrite the RGB tuple of each changed role, in its mode's table.

    `entries` is `(mode, name)` pairs, default every unsaved edit in both
    modes (#334: `palette.py` holds a LIGHT and a DARK table). Returns the
    entries done.
    """
    entries = tuple(entries) if entries is not None else palette.dirty_entries()
    if not entries:
        return ()
    with open(PALETTE_PY) as f:
        lines = f.read().split("\n")

    done = []
    for mode, name in entries:
        r, g, b = palette.TABLES[mode][name]
        lo, hi = _block(lines, mode.upper() + ": ")
        for i in range(lo, hi):
            parsed = _split_key(lines[i])
            if parsed is None or parsed[0] != name:
                continue
            _, indent, head = parsed
            line = lines[i]
            close = line.index(")", len(indent) + len(head))
            hash_at = line.find("#", close)             # the trailing note
            body = f"{indent}{head}: ({r}, {g}, {b}),"
            if hash_at >= 0:
                # keep the comment column where it was, if it still fits
                body += " " * max(1, hash_at - len(body)) + line[hash_at:]
            lines[i] = body
            done.append((mode, name))
            break
        else:
            raise LookupError(f"{name} not found in palette.py's {mode} table")

    with open(PALETTE_PY, "w") as f:
        f.write("\n".join(lines))
    palette.mark_clean()
    return tuple(done)


# --- font -----------------------------------------------------------------

def write_font(entries=None):
    """Rewrite each changed glyph's source row-string. Returns what was done."""
    entries = list(entries) if entries is not None else font.dirty()
    if not entries:
        return ()
    with open(FONT_PY) as f:
        lines = f.read().split("\n")

    small_lo, small_hi = _block(lines, "_SMALL_SRC")
    large_lo, large_hi = _block(lines, "_LARGE_SRC")

    done = []
    for size, ch in entries:
        bits = font._GLYPHS[size][ch]
        lo, hi = (small_lo, small_hi) if size == font.SMALL else (large_lo, large_hi)
        for i in range(lo, hi):
            parsed = _split_key(lines[i])
            if parsed is None or parsed[0] != ch:
                continue
            _, indent, head = parsed
            if size == font.SMALL:
                value = '"%s"' % font.serialize_small(bits)
            else:
                top, spec = font.serialize_large(bits)
                value = '(%d, "%s")' % (top, spec)
            lines[i] = f"{indent}{head}: {value},"
            done.append((size, ch))
            break
        else:
            raise LookupError(f"{size} glyph {ch!r} not found in font.py")

    with open(FONT_PY, "w") as f:
        f.write("\n".join(lines))
    font.mark_clean()
    return tuple(done)


# --- spacing --------------------------------------------------------------

def _replace_block(lines, header, body_lines):
    """Swap the body of the dict literal opened by `header` for body_lines."""
    for i, line in enumerate(lines):
        if line.startswith(header):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("}"):
                    return lines[:i + 1] + body_lines + lines[j:]
            break
    raise LookupError(header)


def write_spacing():
    """Regenerate the two advance tables. They are generated data, so unlike
    the glyph rows they are rewritten whole."""
    if not font.spacing_dirty():
        return False
    with open(FONT_PY) as f:
        lines = f.read().split("\n")

    body = []
    for size in (font.SMALL, font.LARGE):
        table = font._ADVANCE_SRC[size]
        if not table:
            body.append(f"    {size.upper()}: {{}},")
            continue
        # charset order, not dict order: the file should read like the font
        entries = [(ch, table[ch]) for ch in font.charset(size) if ch in table]
        body.append(f"    {size.upper()}: {{")
        for ch, adv in entries:
            body.append(f"        {ch!r}: {adv},")
        body.append("    },")
    lines = _replace_block(lines, "_ADVANCE_SRC", body)

    tab = [f"    {size.upper()}: {font._TABULAR_SRC[size]!r},"
           for size in (font.SMALL, font.LARGE)]
    lines = _replace_block(lines, "_TABULAR_SRC", tab)

    with open(FONT_PY, "w") as f:
        f.write("\n".join(lines))
    font.mark_spacing_clean()
    return True


def write_all():
    return {"palette": write_palette(), "font": write_font(),
            "spacing": write_spacing()}


if __name__ == "__main__":
    print(write_all())
