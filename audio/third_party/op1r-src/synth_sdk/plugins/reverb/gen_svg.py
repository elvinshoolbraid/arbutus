#!/usr/bin/env python3
"""Generate display/kalireverb.svg + fx/kali_geom.h from the artwork display/src/kali-reverb.svg (screen of the
"reverb" FX, fx/reverb.c).

The OP-1 SVG parser knows no CSS and no rotate(): classes become inline attributes, `translate() rotate()` becomes
matrix(), `Z` becomes `z`, rx/ry/data-name are dropped. Groups the screen code switches on / off (node +0x50):
  karm1..6   arms (1 left lower + cup, 2 right lower, 3/4 middle, 5 upper left (sword), 6 upper right (trident))
  kskull1..5 the skull necklace at 5 brightness levels (white knob)
  kfire1..5  fire ring + flames at 5 brightness levels (green knob; none shown at 0)
  kmix0..9   red copies of the mix scale lines, 0 = top (red knob lights them from the bottom)
Needles rotated by code: ktime (blue), kdiff (white), ktone (green: the red tone dial recoloured), pivots in kali_geom.h.
"""
import math, os, re, sys, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'harness'))
from render import path_ops                           # SVG path parser of the host renderer (absolute M L C Z)
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'kali-reverb.svg')
NS = '{http://www.w3.org/2000/svg}'
LEVELS = [0.3, 0.45, 0.6, 0.8, 1.0]                 # brightness of the 5 skull / fire copies
GREEN = '#00ed95'
RED = '#ff3a5d'

def styles(root):
    css = ''.join(e.text or '' for e in root.iter(NS + 'style'))
    out = {}
    for sel, body in re.findall(r'([^{}]+)\{([^}]*)\}', css):
        props = dict((k.strip(), v.strip()) for k, v in (p.split(':') for p in body.split(';') if ':' in p))
        for c in re.findall(r'\.(cls-\d+)', sel):
            out.setdefault(c, {}).update(props)
    return out

def scale(col, f):
    col = col.lstrip('#')
    if col == 'none': return 'none'
    if len(col) == 3: col = ''.join(ch * 2 for ch in col)
    return '#%02x%02x%02x' % tuple(round(int(col[i:i + 2], 16) * f) for i in (0, 2, 4))

def matrix(t):
    m = [1, 0, 0, 1, 0, 0]                          # a b c d e f
    for name, args in re.findall(r'(\w+)\(([^)]*)\)', t):
        v = [float(x) for x in re.findall(r'-?\d*\.?\d+', args)]
        if name == 'translate': n = [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0]
        elif name == 'rotate':
            a = math.radians(v[0]); n = [math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0]
        elif name == 'matrix': n = v
        else: raise ValueError(t)
        a, b, c, d, e, f = m                        # m = m * n
        m = [a * n[0] + c * n[1], b * n[0] + d * n[1], a * n[2] + c * n[3], b * n[2] + d * n[3],
             a * n[4] + c * n[5] + e, b * n[4] + d * n[5] + f]
    return 'matrix(' + ' '.join(('%.4f' % x).rstrip('0').rstrip('.') for x in m) + ')'   # no exponents (OP-1 parser)

def num(v):                                          # plain decimal: leading zero, no exponent
    t = ('%.2f' % v).rstrip('0').rstrip('.')
    return '0' if t in ('-0', '') else t

def simple_path(d, split=True):
    """rewrite path data the way the stock files are written: one command letter per segment, absolute M / L / C / z,
    plain numbers (no implicit repeats, no '1.3.93', no '.5' - the OP-1 parser misreads those)"""
    out, x, y, sx, sy = [], 0.0, 0.0, 0.0, 0.0
    prev_dir = None                                  # end tangent of the previous segment
    for op in path_ops(d):
        if op[0] in 'LC':                            # straight reversal (~180 deg): the OP-1 stroker joins with a
            v = op[1:]                               # mitre -> a long spike; start a new subpath there instead
            first = (v[0] - x, v[1] - y) if op[0] == 'L' else next(((a - x, b - y) for a, b in ((v[0], v[1]), (v[2], v[3]), (v[4], v[5])) if abs(a - x) + abs(b - y) > 1e-6), (0, 0))
            n = math.hypot(*first)
            if split and prev_dir and n > 1e-6 and (first[0] * prev_dir[0] + first[1] * prev_dir[1]) / n < -0.999:
                out.append('M' + num(x) + ',' + num(y)); sx, sy = x, y
            end = (v[-2] - (v[-4] if op[0] == 'C' else x), v[-1] - (v[-3] if op[0] == 'C' else y))
            m = math.hypot(*end)
            prev_dir = (end[0] / m, end[1] / m) if m > 1e-6 else prev_dir
        else:
            prev_dir = None
        if op[0] == 'Z': out.append('z'); x, y = sx, sy; continue
        v = op[1:]
        if op[0] == 'M': out.append('M' + num(v[0]) + ',' + num(v[1])); x, y = sx, sy = v; continue
        rel = [round(a - (x if k % 2 == 0 else y), 2) for k, a in enumerate(v)]
        x, y = x + rel[-2], y + rel[-1]                  # follow the rounded points: no drift
        out.append(op[0].lower() + ''.join((',' if k and not num(a).startswith('-') else '') + num(a) for k, a in enumerate(rel)))
    return ''.join(out)

def emit(el, css, col=None, keep_id=None):
    """element (no groups) -> SVG text with inline attributes; col(f) recolours stroke / fill"""
    tag = el.tag[len(NS):]
    a = {}
    for c in (el.get('class') or '').split():
        a.update(css.get(c, {}))
    if tag != 'line' and tag != 'polyline' and 'fill' not in a: a['fill'] = '#000000'
    if tag in ('line', 'polyline') and 'fill' not in a: a['fill'] = 'none'
    attrs = {k: a[k] for k in ('fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin') if k in a}
    attrs['stroke-width'] = attrs.get('stroke-width', '1').replace('px', '')
    for k in ('fill', 'stroke'):                     # #rrggbb only (no 3-digit colours), recoloured copies
        if attrs.get(k, 'none') != 'none': attrs[k] = (col or (lambda c: scale(c, 1)))(attrs[k])
    geo = {k: v for k, v in el.attrib.items() if k in ('x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'x1', 'y1',
                                                       'x2', 'y2', 'points', 'd')}
    if 'd' in geo: geo['d'] = simple_path(geo['d'], attrs.get('fill', 'none') == 'none')   # filled: keep one outline
    if el.get('transform'): geo['transform'] = matrix(el.get('transform'))
    if keep_id: geo = {'id': keep_id, **geo}
    return '<%s %s/>' % (tag, ' '.join('%s="%s"' % kv for kv in {**geo, **attrs}.items()))

def main():
    root = ET.parse(SRC).getroot()
    css = styles(root)
    groups = {g.get('id'): g for g in root.iter(NS + 'g') if g.get('id')}
    leaves = lambda g: [e for e in g.iter() if e is not g and e.tag != NS + 'g']
    body = list(groups['kali-body'])
    left_lower = [body[i] for i in (23, 24, 25, 26, 27, 28, 30, 31)]          # arm, cup, drops
    right_lower = [body[i] for i in (16, 17, 19)]
    rest = [e for i, e in enumerate(body) if e not in left_lower and e not in right_lower]
    o = ['<?xml version="1.0" encoding="utf-8"?>',
         '<svg version="1.1" id="Layer_1" xmlns="http://www.w3.org/2000/svg" x="0px" y="0px" width="320px" height="160px" viewBox="0 0 320 160">',
         '<!-- generated by display/gen_kali_svg.py from display/src/kali-reverb.svg - reverb FX (Kali) -->']
    def group(gid, elems, col=None):
        o.append(f'<g id="{gid}">')
        o.extend(emit(e, css, col) for e in elems)
        o.append('</g>')
    for k, f in enumerate(LEVELS):                                             # fire, behind everything
        group(f'kfire{k + 1}', leaves(groups['fire']), lambda c, f=f: scale(c, f))
    piv = {}
    for gid, nid in (('time-meter', 'ktime'), ('diffusion-meter', 'kdiff'), ('tone-meter', 'ktone')):
        circ, bar = list(groups[gid])
        col = (lambda c: GREEN if c.lower() == RED else scale(c, 1)) if nid == 'ktone' else None   # tone on green
        o.append(emit(bar, css, col, keep_id=nid)); o.append(emit(circ, css, col))
        piv[nid] = (float(circ.get('cx')), float(circ.get('cy')))
    o.append('<g>'); o.extend(emit(e, css) for e in leaves(groups['meter-scales'])); o.append('</g>')
    group('kmixtitle', leaves(groups['mix-title']), lambda c: RED if c.lower() == GREEN else scale(c, 1))   # mix = red knob
    meter = leaves(groups['mix-meter'])
    o.append('<g>'); o.extend(emit(e, css) for e in meter); o.append('</g>')
    for j, e in enumerate(meter):
        group(f'kmix{j}', [e], lambda c: RED)
    group('kdry', leaves(groups['mix-dry']))
    for gid, name in (('left-upper-arm', 'karm5'), ('right-upper-arm', 'karm6'), ('left-middle-arm', 'karm3'),
                      ('right-middle-arm', 'karm4')):
        group(name, leaves(groups[gid]))
    group('khead', leaves(groups['head-shine']))
    for k, f in enumerate(LEVELS):
        group(f'kskull{k + 1}', leaves(groups['skulls']), lambda c, f=f: scale(c, f))
    group('karm1', left_lower)
    group('karm2', right_lower)
    group('kbody', rest)
    o.append('</svg>')
    open(os.path.join(HERE, 'kalireverb.svg'), 'w').write('\n'.join(o) + '\n')
    h = ['/* generated by display/gen_kali_svg.py - needle pivots of display/kalireverb.svg (Q10) */']
    for nid, (x, y) in piv.items():
        h.append(f'#define KALI_{nid[1:].upper()}_X OP1_Q10({x})')
        h.append(f'#define KALI_{nid[1:].upper()}_Y OP1_Q10({y})')
    h.append(f'#define KALI_LEVELS {len(LEVELS)}')
    h.append(f'#define KALI_MIX_LINES {len(meter)}')
    open(os.path.join(HERE, 'kali_geom.h'), 'w').write('\n'.join(h) + '\n')
    print('-> display/kalireverb.svg, fx/kali_geom.h')

if __name__ == '__main__':
    main()
