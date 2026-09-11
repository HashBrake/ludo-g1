import time

from pxdex.dh13 import DexH13Control

if __name__ == "__main__":
    control = DexH13Control()
    # 连接灵巧手
    handy_type = control.activeHandy('/dev/ttyUSB0', 'none')
    print(f"连接灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
    hz = 10
    # 获取传感器数据(按住指尖传感器,数值才有变化)
    while True:
        finger_tactile_list = control.getFingerTactile()
        for finger_tactile in finger_tactile_list:
            print(f"x:{finger_tactile.x}, y:{finger_tactile.y}, z:{finger_tactile.z}")
        time.sleep(1 / hz)
