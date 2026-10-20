# -*- coding: utf-8 -*-
import sys
p = r"d:\Wang\下载D\duan_LU\duan_LU\dl645_breaker\simulator.py"
a = int(sys.argv[1]); b = int(sys.argv[2])
lines = open(p, encoding="utf-8").read().split("\n")
for i in range(a - 1, min(b, len(lines))):
    print("%d\t%s" % (i + 1, lines[i]))