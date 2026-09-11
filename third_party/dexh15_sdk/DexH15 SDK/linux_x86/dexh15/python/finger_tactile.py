import sys
import time
from pxdex.dh15 import DexH15Control

# 手动连接灵巧手读取触觉数据参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例
        2. 打开 Modbus 串口设备
        3. 初始化从站设备信息
        4. 传感器标定
        5. 读取触觉数据，这里以读取合力为例
        6. 关闭 Modbus 串口
    """

    # 1. 创建串口控制实例
    hand_control = DexH15Control()

    # 串口名，需要根据实际情况调整
    hand_port_num = "/dev/ttyUSB0"
    # 波特率，默认为 4000000
    baud_rate = 4000000
    # 从站地址，需要根据实际情况调整
    slave_address = 0x78

    # 2. 打开 Modbus 串口设备
    if not hand_control.openModbusDevice(hand_port_num, baud_rate):
        print("请检查是否插入 Modbus 串口设备")
        sys.exit(1)
    print("打开 Modbus 串口成功")

    # 3. 初始化从站设备信息
    if hand_control.initModbusDevice(slave_address) != 1:
        print("请检查灵巧手是否接入 Modbus 串口设备")
        hand_control.disconnectModbus()
        sys.exit(1)
    print("初始化 Modbus 从站成功")

    # 4. 传感器标定
    if not hand_control.calibrateSensor(slave_address):
        print("校准传感器失败")
    else:
        print("校准传感器成功")

    hz = 10
    print(f"\n开始读取传感器数据 (频率: {hz}Hz)")
    print("提示: 按住指尖传感器数值才会有变化")
    print("-" * 50)

    # 5. 读取触觉数据，这里以读取合力为例
    try:
        while True:
            force_points = hand_control.getFingerResultantForce(slave_address)
            for force_point in force_points:
                print(f"x:{force_point.x}, y:{force_point.y}, z:{force_point.z}")
            time.sleep(1.0 / hz)
    except KeyboardInterrupt:
        print("\n用户中断程序")
    finally:
        # 6. 关闭 Modbus 串口
        ret = hand_control.disconnectModbus()
        print(f"断开连接: {'成功' if ret else '失败'}")