from pxdex.dh15 import DexH15Control


# 手动连接灵巧手使用错误码参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例
        2. 打开 Modbus 串口设备
        3. 初始化从站设备信息
        4. 初始化电机位置（后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可）
        5. 检查灵巧手状态
        6. 查询电机错误码
        7. 清除电机错误码
        8. 关闭 Modbus 串口
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
        print("打开串口失败")
        sys.exit(1)
    print("打开串口成功")

    # 3. 初始化从站设备信息
    if hand_control.initModbusDevice(slave_address) != 1:
        print("初始化从站设备失败")
        sys.exit(1)
    print("初始化从站设备成功")

    # 4. 初始化电机位置（后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可）
    if hand_control.initMotorPosition(slave_address) != 1:
        print("初始化电机失败")
        sys.exit(1)
    print("初始化电机成功")

    # 5. 检查灵巧手状态
    hand_status = hand_control.checkHandStatus(slave_address)
    print(f"灵巧手当前状态为：{'正常运行状态' if hand_status == 1 else '容错运行状态' if hand_status == 2 else '不可运行状态'}")

    # 6. 查询电机错误码
    fault_list = hand_control.getFaultCode(slave_address)
    for fault in fault_list:
        print(f"手指：{fault.finger_name}, 电机：{fault.motor_ID}，错误码：{fault.error_code}")
    
    # 7. 清除电机错误码
    ret = hand_control.clearFaultCode(slave_address)
    print(f"清除错误码：{'成功' if ret else '失败'}")

    # 8. 关闭 Modbus 串口
    ret = hand_control.disconnectModbus()
    print(f"断开连接: {'成功' if ret else '失败'}")
