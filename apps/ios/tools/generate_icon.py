"""Rasterize Can's original geometric vector mark for the iOS AppIcon catalog.

No external image, font, network service, or dependency is used.
"""
from pathlib import Path
import math
import struct
import zlib

size = 1024
background = (18, 23, 25)
accent = (189, 232, 111)
angle = math.atan2(173, 228)
radius = math.hypot(228, 173)
caps = [(228, 173), (228, -173)]

def inside(x, y):
    distance = math.hypot(x, y)
    return ((abs(distance - radius) <= 57.5 and abs(math.atan2(y, x)) >= angle)
            or any((x-cx)**2 + (y-cy)**2 <= 57.5**2 for cx, cy in caps)
            or (x-312)**2 + y*y <= 45**2)

pixels = bytearray()
for y in range(size):
    pixels.append(0)
    for x in range(size):
        coverage = sum(inside(x+dx-512, y+dy-512) for dx,dy in [(0.25,0.25),(0.75,0.25),(0.25,0.75),(0.75,0.75)]) / 4
        pixels.extend(round(b+(a-b)*coverage) for b,a in zip(background, accent))

def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))

png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size,size,8,2,0,0,0)) + chunk(b'IDAT', zlib.compress(pixels,9)) + chunk(b'IEND',b'')
path = Path(__file__).resolve().parents[1] / 'Can/Assets.xcassets/AppIcon.appiconset/CanIcon.png'
path.write_bytes(png)
print(f'Wrote {path.name}: {size}x{size}, RGB, {len(png)} bytes')
