import sys
import time
from pxdex.dh15 import DexH15Control, Dex15MotorPosition, ControlMode

# 手动连接灵巧手使用自定义动作参考示例：
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例
        2. 打开 Modbus 串口
        3. 初始化从站设备信息
        4. 初始化电机位置(如果刚上电时已调用，可跳过)
        5. 设置位置模式
        6. 使能电机
        7. 执行内置官方动作
        8. 电机下使能
        9. 关闭串口
    """

    # 1. 创建串口控制实例
    hand_control = DexH15Control()

    # 串口名，需要根据实际情况调整
    hand_port_num = "/dev/ttyUSB0"
    # 波特率，默认为 4000000
    baud_rate = 4000000
    # 从站地址，需要根据实际情况调整
    slave_address = 0x78

    # 2. 打开 Modbus 串口
    if not hand_control.openModbusDevice(hand_port_num, baud_rate):
        print("请检查是否插入 Modbus 串口设备")
        sys.exit(1)
    print("打开 Modbus 串口成功")

    # 3. 初始化从站设备信息
    if hand_control.initModbusDevice(slave_address) != 1:
        print("请检查灵巧手是否接入 Modbus 串口设备")
        sys.exit(1)
    print("初始化 Modbus 从站成功")

    # 4. 初始化电机位置(如果刚上电时已调用，可跳过)
    if hand_control.initMotorPosition(slave_address) != 1:
        print("初始化电机位置失败")
        sys.exit(1)
    print("初始化电机位置成功")

    # 5. 设置位置模式
    if not hand_control.setMotorControlMode(slave_address, ControlMode.POSITION_CONTROL_MODE):
        print("设置位置模式失败")
        sys.exit(1)
    print("设置位置模式成功")

    # 6. 使能电机
    if not hand_control.enableMotor(slave_address):
        print("使能电机失败")
        sys.exit(1)
    print("使能电机成功")

    # 零位
    zero_positions = Dex15MotorPosition()

    # 7. 执行内置官方动作，执行动作 1-10
    for i in range(1, 11):
        if not hand_control.runAction(slave_address, i, False):
            print(f"执行动作 {i} 失败")
        else:
            print(f"执行动作 {i} 成功")
        
        time.sleep(1)
        hand_control.setMotorTargetPosition(slave_address, zero_positions)
        time.sleep(1)

    # 8. 电机下使能
    hand_control.disableMotor(slave_address)
    print("电机下使能成功")

    # 9. 关闭串口
    is_success = hand_control.disconnectModbus()
    print(f"断开连接：{' 成功' if is_success else ' 失败'}")