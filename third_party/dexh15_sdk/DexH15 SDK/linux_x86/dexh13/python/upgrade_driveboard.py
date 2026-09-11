from pxdex.dh13 import DexH13Control
from pxdex.dh13 import UpgradeFileAndPart


if __name__ == "__main__":
    control = DexH13Control()
    # 激活灵巧手
    handy_type = control.activeHandy("/dev/ttyUSB0", "none")
    print(f"激活灵巧手成功，灵巧手类型为{'左手' if handy_type == 1 else '右手'}")
    # 升级电驱版本
    two_other_upgrade_file = UpgradeFileAndPart()
    two_other_upgrade_file.upgrade_part = {0x0201, 0x0202, 0x0203, 0x0204}
    two_other_upgrade_file.file_path = "AX58400_DexH13_MOTOR_V1.3.4_TWO_OTHER_SH.efw"
    one_other_upgrade_file = UpgradeFileAndPart()
    one_other_upgrade_file.upgrade_part = {0x0101, 0x0102, 0x0103}
    one_other_upgrade_file.file_path = "AX58400_DexH13_MOTOR_V1.1.8_ONE_OTHER_SH.efw"
    one_thumb_upgrade_file = UpgradeFileAndPart()
    one_thumb_upgrade_file.upgrade_part = {0x0104, 0x0105}
    one_thumb_upgrade_file.file_path = "AX58400_DexH13_MOTOR_V1.1.8_ONE_THUMB_SH.efw"
    result = control.upgradeDriveboard([two_other_upgrade_file, one_other_upgrade_file, one_thumb_upgrade_file])
    print(f"升级电驱版本，结果为{result}")
    # 断开灵巧手连接，升级完成后，需要断电重启灵巧手
    is_success = control.disconnectHandy()
    print(f"断开灵巧手连接{'成功' if is_success else '失败'}")
