# -*- coding: utf-8 -*-
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from dl645_breaker import gui as G
from dl645_breaker.simulator import VirtualBreaker
from dl645_breaker.device import BreakerClient, VirtualTransport

app = G.App()
app.update()
vb = VirtualBreaker()
app.worker.client = BreakerClient(VirtualTransport(vb), '000000000001')
app.worker.connected = True

app._read_device_passwords()
for _ in range(30):
    app.update(); time.sleep(0.04)

print('pwd页 =', app.var_pwd_diag.get())
print('控制页=', app.var_ctrl_pwd_hint.get())
assert '0级密码=000000' in app.var_ctrl_pwd_hint.get()
app.destroy()
print('GUI2 SMOKE OK')
ld