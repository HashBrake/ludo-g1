import time
from pxdex.dh5 import DexH5Control

class Controller:
    def __init__(self,hand_port_num="/dev/ttyUSB0"):
        self.control = DexH5Control()

        # Activate and enable the Hand
        hid = self.control.connectDevice(hand_port_num)
        print("Hand type: ",hid)

        # Verify if the activation has succeeded
        isConnected = self.control.isConnected()
        print("isConnected:", isConnected)
        if not isConnected:
            raise RuntimeError("ConnectDevice failed. Please try again.")

        isMotorEnabled = self.control.isMotorEnabled()
        print("isMotorEnabled:", isMotorEnabled)
        if not isMotorEnabled:
            raise RuntimeError("MotorEnabled failed. Please try again.")
        time.sleep(1)
        
    def check_health(self):
        ##Identify the Version
        getVersion = self.control.getVersion()
        print(f"getSDKVersion: {getVersion.sdk_version}, getFirmwareVersion: {getVersion.firmware_version}")

        # Check for hardware failures
        isFault = self.control.isFault()
        print("isFault:", isFault)

        #  Identify the cause of the failure
        if isFault:
            getFaultCode = self.control.getFaultCode()
            for fault in getFaultCode:
                if isinstance(fault, list):
                    for code in fault:
                        print("getFaultCode:", code)
                else:
                    print("getHReturnCode:", fault)
            raise RuntimeError("\n !!! Fault detected. Please check that the dexterous hand is in proper working condition before operation. \n")
        
    def set_joint_positions(self, target_list):
        setJointPositionsDegree = self.control.setJointPositionsDegree(target_list)
        print("setJointPositionsDegree: ", setJointPositionsDegree)
        time.sleep(2)
        self.currentJointDegree = self.control.getJointPositionsDegree()
        print("getJointPositionsAngle: ",self.currentJointDegree)



def main():
    print("--- ----- --- Dexterous Hand Test Start --- ----- --- ")

    dexh5 = Controller("/dev/ttyUSB0")
    dexh5.check_health()

    target0 = [0,0,0,0,0]  
    target1 = [0,0,75,0,40]  
    target2 = [47,75,75,47,29]

    while True:
        dexh5.set_joint_positions(target0)
        dexh5.set_joint_positions(target1)
        dexh5.set_joint_positions(target2)


if __name__ == "__main__":
    main()