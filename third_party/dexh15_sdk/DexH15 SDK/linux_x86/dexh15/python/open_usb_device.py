import sys
from pxdex.dh15 import DexH15Control


# USB 直连使用参考示例
if __name__ == "__main__":
    """
    步骤示例：
        1. 打开 USB 串口【必选】
        2. 初始化电机位置【后续如果有位置控制，该操作为必要项，在上电连接设备后调用一次即可】
        3. 其他操作，例如：查询灵巧手在线状态【可选】
        4. 关闭 USB 串口【必选】
    """

    # 1. 创建串口控制实例
    hand_control = DexH15Control()

    # USB 串口号
    hand_port_num = "/dev/ttyACM0"

    # 打开 USB 串口
    ret, slave_address = hand_control.openUsbDevice(hand_port_num)
    if not ret:
        print("请检查是否插入 USB 串口设备")
        sys.exit(1)
    print("打开 USB 串口成功")

    # 3. 初始化电机位置(如果刚上电时已调用，可跳过)
    if hand_control.initMotorPosition(slave_address) != 1:
        print("初始化电机位置失败")
        hand_control.disconnectUsb()
        sys.exit(1)
    print("初始化电机位置成功")

    # 4. 查询灵巧手在线状态
    is_connected = hand_control.isModbusDeviceConnected(slave_address)
    print(f"灵巧手{'在线' if is_connected else '离线'}")

    # 5. 关闭 USB 串口
    result = hand_control.disconnectUsb()
    print(f"断开连接：{' 成功' if result else ' 失败'}")