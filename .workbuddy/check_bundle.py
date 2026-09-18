# -*- coding: utf-8 -*-
s = open(r'D:\agents_project\清库方案自动计算\plugin_app\bundle.js', encoding='utf-8').read()
BS = chr(92)  # backslash


def esc(t):
    return ''.join((BS + 'u%04X' % ord(c)) if ord(c) > 127 else c for c in t)


checks = {
    '无 compPoolOwm 残留': 'compPoolOwm' not in s,
    '无 poolDtc 残留': 'poolDtc' not in s,
    '含单池 compPool': 'compPool' in s,
    '含最优窗口 bestWindow': 'bestWindow' in s,
    '含最优窗口字段名': esc('最优窗口期(天)') in s,
    '含单行挂载 anchorRowId': 'anchorRowId' in s,
    '含不清库排除': esc('不清库') in s,
}
for k, v in checks.items():
    print(('PASS' if v else 'FAIL'), k)
import os
print('bundle size:', os.path.getsize(r'D:\agents_project\清库方案自动计算\plugin_app\bundle.js'))
