from pxdex.dh13 import DexH13Control
from pxdex.dh13 import UpgradeFileAndPart

if __name__ == "__main__":
    control = DexH13Control()
    # 激活灵巧手
    handy_type = control.activeHandy("/dev/ttyUSB0", "none")
    print(f"激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
    # 升级主控版本
    result = control.upgradeMainboard(["AX58400_DexH13_CM4_V2.0.0.efw", "AX58400_DexH13_CM7_V2.0.0.efw"])
    print(f"升级主控版本，结果为{result}")
    # 断开灵巧手连接，升级完成后，需要断电重启灵巧手
    is_success = control.disconnectHandy()
    print(f"断开灵巧手连接{'成功' if is_success else '失败'}")    