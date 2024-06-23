#!/bin/python3

import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
import colorsys

n_i = 16
n_c = 16
rs, gs, bs = [], [], []
data = np.empty((n_i,n_c,3), dtype=np.uint8)
for i in range(n_i):
    for c in range(n_c):
        r, g, b = colorsys.hls_to_rgb(float(c)/n_c, float(1.35**(i+1))/(1.35**n_i), 0.75)
        rs.append(int(r*255))
        gs.append(int(g*255))
        bs.append(int(b*255))
        data[i,c,:] = (rs[-1], gs[-1], bs[-1])

print(rs)
print(gs)
print(bs)

fig, ax = plt.subplots()
ax.imshow(data)

# draw gridlines
ax.grid(which='major', axis='both', linestyle='-', color='k', linewidth=2)
ax.set_xticks(np.arange(-.5, 16, 1));
ax.set_yticks(np.arange(-.5, 16, 1));

plt.show()
