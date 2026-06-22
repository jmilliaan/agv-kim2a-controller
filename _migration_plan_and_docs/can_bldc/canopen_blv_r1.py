import canopen
import time

network = canopen.Network()
network.connect(interface="slcan", channel="COM16", bitrate=500000)

node = canopen.BaseNode402(1, "BLVD-KRD_CANopen_V400.eds")
network.add_node(node)

node.nmt.state = "PRE-OPERATIONAL"
time.sleep(0.3)
node.nmt.state = "OPERATIONAL"
time.sleep(0.3)

if node.state == "FAULT":
    node.fault_reset()
    time.sleep(0.5)

node.state = "READY TO SWITCH ON"
time.sleep(0.5)
node.state = "SWITCHED ON"
time.sleep(0.5)
node.state = "OPERATION ENABLED"
time.sleep(0.5)

node.sdo["Modes of operation"].raw = 3
time.sleep(0.1)
node.sdo["Profile acceleration"].raw = 2000
node.sdo["Profile deceleration"].raw = 2000
# node.sdo["Target velocity"].raw = 3000
increment = 50
start = 300
for i in range(20):
    target_v = start + increment * i
    print(target_v)
    node.sdo["Target velocity"].raw = target_v
    time.sleep(0.5)

node.sdo["Target velocity"].raw = 0
time.sleep(1)
node.state = "SWITCHED ON"
network.disconnect()