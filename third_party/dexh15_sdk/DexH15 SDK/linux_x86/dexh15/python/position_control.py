import sys
import time
from pxdex.dh15 import DexH15Control, Dex15MotorPosition, ControlMode

# 手动连接灵巧手进行位控参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例
        2. 打开 Modbus 串口
        3. 初始化 Modbus 从站
        4. 初始化电机位置(如果刚上电时已调用，可跳过)
        5. 查询灵巧手在线状态
        6. 设置位置模式 
        7. 使能电机   
        8. 查询使能状态 
        9. 位置控制（这里以设置电机位置接口 setMotorTargetPosition 为例，还可通过角度设置接口 setJointPositionsAngle 控制）
        10. 电机回零
        11. 下使能电机 
        12. 关闭 Modbus 串口
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

    # 3. 初始化 Modbus 从站
    if hand_control.initModbusDevice(slave_address) != 1:
        print("请检查灵巧手是否接入 Modbus 串口设备")
        sys.exit(1)
    print("初始化 Modbus 从站成功")

    # 4. 初始化电机位置(如果刚上电时已调用，可跳过)
    if hand_control.initMotorPosition(slave_address) != 1:
        print("初始化电机位置失败")
        sys.exit(1)
    print("初始化电机位置成功")

    # 5. 查询灵巧手在线状态
    is_connected = hand_control.isModbusDeviceConnected(slave_address)
    print(f"灵巧手连接状态为{'在线' if is_connected else '离线'}")

    # 6. 设置位置模式
    if not hand_control.setMotorControlMode(slave_address,  ControlMode.POSITION_CONTROL_MODE):
        print("设置位置模式失败，无法进行位置模式控制")
        sys.exit(1)
    print("设置位置模式成功")

    # 7. 使能电机
    if not hand_control.enableMotor(slave_address):
        print("使能电机失败，无法进行位置模式控制")
        sys.exit(1)
    print("使能电机成功")

    # 8. 查询使能状态
    if not hand_control.isMotorEnabled(slave_address):
        print("电机状态为未使能，无法进行位置模式控制")
        sys.exit(1)
    print("电机状态为已使能")

    # 9. 位置控制（这里以设置电机位置接口 setMotorTargetPosition 为例，还可通过角度设置接口 setJointPositionsAngle 控制）
    positions = Dex15MotorPosition()
    positions.motor1_pos = 8000
    positions.motor2_pos = 8000
    positions.motor3_pos = 8000
    positions.motor4_pos = 8000
    positions.motor5_pos = 3000
    positions.motor6_pos = 5000
    positions.motor7_pos = 5000

    if hand_control.setMotorTargetPosition(slave_address, positions) != 1:
        print("设置电机位置失败")
        sys.exit(1)
    print("设置电机位置成功")
    # 等待1秒
    time.sleep(1)

    # 10. 电机回到零位
    zero_positions = Dex15MotorPosition()
    if hand_control.setMotorTargetPosition(slave_address, zero_positions) != 1:
        print("电机位置回零失败")
    else:
        print("电机位置回零成功")
    # 等待1秒
    time.sleep(1)

    # 11. 电机下使能
    is_success = hand_control.disableMotor(slave_address)
    print(f"电机下使能{'成功' if is_success else '失败'}")

    # 12. 关闭 Modbus 串口
    is_success = hand_control.disconnectModbus()
    print(f"断开连接：{' 成功' if is_success else ' 失败'}")