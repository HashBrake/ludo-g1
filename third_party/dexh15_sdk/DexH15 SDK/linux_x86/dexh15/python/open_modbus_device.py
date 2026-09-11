import sys
from pxdex.dh15 import DexH15Control


# 手动连接方式使用参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 创建串口控制实例【必要】                              
        2. 打开 Modbus 串口【必要】                                
        3. 扫描设备地址【可选】
        4. 初始化从站设备地址【必要】                                
        5. 初始化电机位置【后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可】 
        6. 其他操作，例如：连接判断【可选】                              
        7. 关闭 Modbus 串口【必要】
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
        
    # 3. 扫描设备地址（已知从站设备地址则可省略该步骤）
    address_list: list[int] = hand_control.scanModbusDevices(hand_port_num)
    if not address_list:
        print("未找到任何设备")
        sys.exit(1)
    # 按需选择通讯的从站设备地址
    slave_address = address_list[0]
        
    # 4. 初始化从站设备地址
    if hand_control.initModbusDevice(slave_address) != 1:
        print("请检查灵巧手是否接入 Modbus 串口设备")
        sys.exit(1)
    print("初始化 Modbus 从站成功")
        
    # 5. 初始化电机位置【后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可】
    if hand_control.initMotorPosition(slave_address) != 1:
        print("初始化电机位置失败")
        sys.exit(1)
    print("初始化电机位置成功")
        
    # 6. 其他操作，例如：连接判断
    is_connected = hand_control.isModbusDeviceConnected(slave_address)
    print(f"灵巧手{'在线' if is_connected else '离线'}")
        
    # 7. 关闭 Modbus 串口
    result = hand_control.disconnectModbus()
    print(f"断开连接：{' 成功' if result else ' 失败'}")