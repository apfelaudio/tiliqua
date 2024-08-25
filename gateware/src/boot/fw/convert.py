#!/bin/python3

import os

with open("apfelaudio.csv", "r") as f:
    coords = []
    for line in f.readlines():
        x, y, _ = line.split(",")
        xr = float(x)
        yr = float(y)
        coords.append( (xr, yr) )

    x0, y0 = coords[0]
    x_min = x_max = x0
    y_min = y_max = y0
    for x, y in coords:
        if x < x_min:
            x_min = x
        if x > x_max:
            x_max = x
        if y < y_min:
            y_min = y
        if y > y_max:
            y_max = y

    x_mid = (x_max + x_min) / 2.0
    y_mid = (y_max + y_min) / 2.0

    print(f"const COORDS: [(i16, i16); {len(coords)}] = [");

    for x, y in coords:
        x_rel = 4*int(x - x_mid)
        y_rel = 4*int(y - y_mid)
        print(f"\t({x_rel}, {y_rel}),")

    print(f"]\n");

    """
    print("xm", x_min, x_max)
    print("ym", y_min, y_max)
    print("mid", x_mid, y_mid)
    """
