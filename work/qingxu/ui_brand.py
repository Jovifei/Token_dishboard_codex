"""Qingxu original continuity mark. Tk, PNG and ICO use only the stdlib.

The linked loops depict continuing work without loading the entire chat.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import math
from pathlib import Path
import struct
import tkinter as tk
import zlib


def icon_svg() -> str:
    """Editable 256-unit vector master for Figma or other design software."""
    return '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
<defs><linearGradient id="bg" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#38A0FF"/><stop offset="1" stop-color="#0067DC"/></linearGradient></defs>
<rect x="3" y="3" width="250" height="250" rx="57" fill="url(#bg)"/>
<path d="M128 128 C105 96 80 94 64 113 C45 135 58 161 82 161 C104 161 114 142 128 128 C145 108 154 95 177 95 C203 95 213 123 193 146 C176 167 150 159 128 128" fill="none" stroke="#fff" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="195" cy="83" r="9" fill="#C9E9FF"/>
</svg>'''


def _bezier(points, n=20):
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        yield (mt**3 * points[0][0] + 3*mt**2*t*points[1][0] + 3*mt*t*t*points[2][0] + t**3*points[3][0],
               mt**3 * points[0][1] + 3*mt**2*t*points[1][1] + 3*mt*t*t*points[2][1] + t**3*points[3][1])


def _loop_points():
    segments = [
        ((128,128),(105,96),(80,94),(64,113)),
        ((64,113),(45,135),(58,161),(82,161)),
        ((82,161),(104,161),(114,142),(128,128)),
        ((128,128),(145,108),(154,95),(177,95)),
        ((177,95),(203,95),(213,123),(193,146)),
        ((193,146),(176,167),(150,159),(128,128)),
    ]
    return [(x/256,y/256) for seg in segments for x,y in _bezier(seg,10)]


def _line_distance(x, y, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx-ax, by-ay
    denom = dx*dx+dy*dy
    t = max(0., min(1., ((x-ax)*dx+(y-ay)*dy)/denom)) if denom else 0.
    return math.hypot(x-(ax+dx*t), y-(ay+dy*t))


def _round_rect_inside(x, y, corner=.23):
    d = corner
    xx = max(d-x,0.,x-(1.-d))
    yy = max(d-y,0.,y-(1.-d))
    return xx*xx+yy*yy <= d*d


def _rgba(size):
    points = _loop_points()
    result = bytearray()
    for yi in range(size):
        for xi in range(size):
            x,y=(xi+.5)/size,(yi+.5)/size
            if not _round_rect_inside(x,y):
                result.extend((0,0,0,0))
                continue
            q=.65*x+.35*y
            r,g,b=int(56*(1-q)),int(160*(1-q)+103*q),int(255*(1-q)+220*q)
            if .17 < x < .84 and .30 < y < .69 and any(
                _line_distance(x,y,a,b)<.0315 for a,b in zip(points,points[1:])):
                r=g=b=255
            elif (x-.7617)**2+(y-.3242)**2<.035**2:
                r,g,b=204,235,255
            result.extend((r,g,b,255))
    return bytes(result)


def _chunk(kind, body):
    return (struct.pack('>I',len(body))+kind+body+
            struct.pack('>I',zlib.crc32(kind+body)&0xffffffff))


@lru_cache(maxsize=5)
def icon_png(size=64):
    if not 16<=size<=256:
        raise ValueError('The Qingxu icon supports sizes 16..256')
    rgba=_rgba(size)
    raw=b''.join(b'\x00'+rgba[y*size*4:(y+1)*size*4] for y in range(size))
    return (b'\x89PNG\r\n\x1a\n'+
            _chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,6,0,0,0))+
            _chunk(b'IDAT',zlib.compress(raw,9))+_chunk(b'IEND',b''))


def write_ico(destination):
    """Create a Windows icon from six PNG frames with no build dependency."""
    entries=[(s,icon_png(s)) for s in (16,32,48,64,128,256)]
    offset=6+16*len(entries)
    table=[]
    for size,content in entries:
        table.append(struct.pack('<BBBBHHII',
              0 if size==256 else size,0 if size==256 else size,
              0,0,1,32,len(content),offset))
        offset+=len(content)
    path=Path(destination)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(struct.pack('<HHH',0,1,len(entries))+
                     b''.join(table)+b''.join(p for _,p in entries))
    return path


def set_window_icon(window):
    """Retain PhotoImage to avoid Tk silently losing the window icon."""
    photo=tk.PhotoImage(master=window,
                        data=base64.b64encode(icon_png(64)).decode('ascii'))
    window.iconphoto(True,photo)
    window._qingxu_icon_photo=photo
    return photo


class BrandMark(tk.Canvas):
    """An image-backed label with no click or focus side-effects."""
    def __init__(self,parent,*,size=36,background='#F5F5F7'):
        super().__init__(parent,width=size,height=size,background=background,
                         borderwidth=0,highlightthickness=0,takefocus=0)
        self.size=size
        self.image=tk.PhotoImage(master=self,
             data=base64.b64encode(icon_png(max(16,min(256,size)))).decode('ascii'))
        self.create_image(size/2,size/2,image=self.image)
