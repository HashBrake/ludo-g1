from pxdex.dh15 import DexH15Control


# 自动连接方式使用参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例【必要】                              
        2. 自动连接设备【必要】                                
        3. 初始化电机位置【后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可】 
        4. 其他操作，例如：连接判断【可选】                              
        5. 关闭 Modbus 串口【必要】
    """

    # 1. 创建串口控制实例
    hand_control = DexH15Control()

    handy_port_num = "/dev/ttyUSB0"
    # 2. 自动连接设备
    connect_ret, slave_address = hand_control.connectModbusAuto(handy_port_num)
    print(f"自动连接: {'成功' if connect_ret else '失败'}")
    if not connect_ret:
        sys.exit(1)
    
    # 3. 初始化电机位置【后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可】
    init_motor_ret = hand_control.initMotorPosition(slave_address)
    if init_motor_ret != 1:
        print("初始化电机位置失败")
        sys.exit(1)
    print("初始化电机位置成功")

    # 4. 其他操作，例如：连接判断
    is_connect = hand_control.isModbusDeviceConnected(slave_address)
    print(f"当前与灵巧手的连接状态为: {'连接' if is_connect else '断开'}")

    # 5. 关闭 Modbus 串口
    ret = hand_control.disconnectModbus()
    print(f"断开连接: {'成功' if ret else '失败'}")







