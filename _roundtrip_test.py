# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from duan_LU.dl645_breaker import protocol as P
from duan_LU.dl645_breaker.simulator import VirtualBreaker
from duan_LU.dl645_breaker.device import BreakerClient, VirtualTransport, make_password, make_operator

# 1) 线序校验: 三相电压 DI=(DI3=02,DI2=01,DI1=FF,DI0=00) 应上线为 [00,FF,01,02]
freq = P.build_read_request(P.encode_addr("000000000001"), (0x02, 0x01, 0xFF, 0x00))
parsed = P.parse_frame(freq)
print("读请求 data(线上) =", [hex(b) for b in parsed["data"]])
assert parsed["data"] == [0x00, 0xFF, 0x01, 0x02], "DI 线序错误!"

# 2) 端到端仿真往返
dev = VirtualBreaker()
client = BreakerClient(VirtualTransport(dev), "000000000001")

# 字节序约定: 多字节BCD数值量 低字节在前(DL645), 分量型字段按书写顺序
assert P.bcd_decode_lf([0x20, 0x23]) == 2320, "bcd_decode_lf 解码错误"
assert P.bcd_encode_lf(2320, 2) == [0x20, 0x23], "bcd_encode_lf 编码错误"
assert P.bcd_encode_lf(1000, 2) == [0x00, 0x10], "1000mA 应为低先 [00 10]"
assert P.bcd_encode_lf(60, 2) == [0x60, 0x00], "60ms 应为低先 [60 00]"
assert P.bcd_decode([0x22, 0x00]) == 2200, "分量型仍按书写顺序解码"

for di, name, expect in [
    ((0x02, 0x01, 0xFF, 0x00), "三相电压", [0x00, 0x22, 0x98, 0x21, 0x01, 0x22]),
    ((0x02, 0x02, 0xFF, 0x00), "三相电流", [0x25, 0x01, 0x00, 0x18, 0x01, 0x00,
                                            0x20, 0x01, 0x00]),
    ((0x02, 0x90, 0xFF, 0x00), "剩余电流", [0x01, 0x25, 0x00]),
    ((0x02, 0x91, 0xFF, 0x00), "额定值", [0x00, 0x03, 0x00, 0x01]),
    ((0x02, 0x92, 0x01, 0x00), "电流方向", [0x00]),
    ((0x04, 0x00, 0x05, 0x01), "运行状态字1", [0x20]),
    ((0x04, 0x00, 0x05, 0x02), "运行状态字2", [0x00]),
    ((0x04, 0x00, 0x04, 0x01), "通信地址", [0x01, 0x00, 0x00, 0x00, 0x00, 0x00]),
    ((0x03, 0x30, 0x01, 0x00), "数据清零总次数", [0x00, 0x00, 0x00]),
    ((0x03, 0x81, 0x00, 0xFF), "跳闸次数", None),
]:
    d = client.read(di)
    print("%-20s -> %s" % (name, [hex(x) for x in d]))
    assert d, name + " 读取为空!"
    if expect is not None:
        assert d == expect, "%s 字节序/内容错误: %s != %s" % (
            name, d, expect)

# 3) 写控制字1 (2级密码)
ok = client.write((0x04, 0x00, 0x10, 0x01), [0x5A],
                  password=make_password(2, "000000"),
                  operator=make_operator("00000001"))
print("写控制字1 ->", ok)
assert ok
assert dev.ctrl1 == 0x5A

# 3b) 写数值量参数(过电压整定 264.0V -> 2640 = BCD 0x2640, 低先 [40 26])
ok = client.write((0x04, 0x00, 0x13, 0x01), [0x40, 0x26],
                  password=make_password(2, "000000"),
                  operator=make_operator("00000001"))
print("写过电压整定 ->", ok, "dev.over_v =", dev.over_v)
assert ok and abs(dev.over_v - 264.0) < 1e-6
d = client.read((0x04, 0x00, 0x13, 0x01))
print("回读过电压整定 ->", [hex(x) for x in d])
assert d == [0x40, 0x26], "数值量参数写读不一致!"

# 4) 控制指令: 远程跳闸 (2级密码, 立即执行 = NN,uu 两字节)
#    规约要求控制 DI 必须带 NN(预约)+uu(单位); NN=0 立即执行。
ok = client.control((0x06, 0x01, 0x01, 0x01), [0x00, 0x00],
                    make_password(2, "000000"), make_operator("00000001"))
print("远程跳闸 ->", ok, "闸位(0=分)", dev.switch_state)
assert ok
frame = P.build_control_request(P.encode_addr("000000000001"),
                                (0x06, 0x01, 0x01, 0x01),
                                make_password(0, "000000"),
                                make_operator("00000000"), [0x00, 0x00])
assert len(P.parse_frame(frame)["data"]) == 14, "立即控制帧数据域应=14字节"

# 5) 修改密码 (2级 -> 2级)
r = client.change_password(make_password(2, "000000"), make_password(2, "123456"))
print("修改密码返回 ->", [hex(x) for x in r])

# 6) 错误表校验
print("ERR_TABLE 0x02 =", P.ERR_TABLE[0x02], "| 0x04 =", P.ERR_TABLE[0x04])
assert P.ERR_TABLE[0x02] == "无请求数据项"
assert P.ERR_TABLE[0x04] == "密码错 / 未授权"

print("\nALL TESTS PASSED")