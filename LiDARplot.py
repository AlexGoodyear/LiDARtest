import argparse
from socket import timeout
import serial
import time
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, Slider
import multiprocessing as mp

###
### Serial port variables
###
SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUDRATE = 115200

###
### Scan Characteristics
###
SCAN_STEPS = 15			 # How many steps/frames per full 360deg scan
SCAN_DEGS = (360.0 / SCAN_STEPS) # Degrees per SCAN_STEP

###
### Received value scaling
###
ROTATION_SPEED_SCALE = 0.05 * 60	# Convert to RPM (LSB: 0.05 rps)
ANGLE_SCALE = 0.01			# Convert to degrees (LSB: 0.01 degrees)
RANGE_SCALE = 0.25 * 0.001		# Convert to meters (LSB: 0.25 mm)

###
### Scan variables
###
#scanSamplesSignalQuality= [0.0]
scanSamplesRange = [0.0]
scanSamplesAngle = [0.0]

###
### Parse the application arguments.
###
parser = argparse.ArgumentParser(description="Extract device name and serial speed for a serial port.")
parser.add_argument("-d", "--device", default=SERIAL_PORT, help="The device name of the serial port")
parser.add_argument("-s", "--speed", type=int, default=SERIAL_BAUDRATE, help="The serial baud rate")
args = parser.parse_args()

###
### Open the serial port to receive data from the LiDAR unit.
###
try:
    lidarSerial = serial.Serial(args.device, args.speed, timeout=0)
except serial.serialutil.SerialException:
    print("ERROR: Serial Connect Error")
    exit()

###
### Constant frame values
###
FRAME_HEADER = 0xAA	# Frame Header value
PROTOCOL_VERSION = 0x01	# Protocol Version value
FRAME_TYPE = 0x61	# Frame Type value

###
### Delta-2G frame structure
###
class Delta2GFrame:
    frameHeader = 0	# Frame Header: 1 byte
    frameLength = 0	# Frame Length: 2 bytes, header to checksum (excluded)
    protocolVersion = 0	# Protocol Version: 1 byte
    frameType = 0	# Frame Type: 1 byte
    commandWord = 0	# Command Word: 1 byte
    parameterLength = 0	# Parameter Field Length: 2 bytes
    parameters = [0]	# Parameter Field
    checksum = 0	# Checksum: 2 bytes

frameIndex = 0

def LiDARFrameProcessing(frame: Delta2GFrame, buffer, do_plot):
    global frameIndex
    global scanSamplesRange
    global scanSamplesAngle
    if (frame.commandWord == 0xAE):
        ###
        ### Device Health Information: Speed Failure
        ###
        rpm = frame.parameters[0] * ROTATION_SPEED_SCALE
        print("Health RPM: %f length: %i" % (rpm, frame.parameterLength))
        frameIndex = 0
    else:
        ###
        ### 1st: Rotation speed (1 byte)
        ###
        rpm = frame.parameters[0] * ROTATION_SPEED_SCALE

        ###
        ### 2nd: Zero Offset angle (2 bytes)
        ###
        offsetAngle = (frame.parameters[1] << 8) + frame.parameters[2]
        rawOffset=offsetAngle
        offsetAngle = offsetAngle * ANGLE_SCALE

        ###
        ### 3rd: Start angle of current data frame (2 bytes)
        ###
        startAngle = (frame.parameters[3] << 8) + frame.parameters[4]
        startAngle = startAngle * ANGLE_SCALE

        ###
        ### Calculate number of samples in current frame
        ###
        sampleCnt = int((frame.parameterLength - 5) / 3)

        ###
        ### 4th: LiDAR samples, each sample has:
        ### Signal Value/Quality (1 byte), Distance Value (2 bytes)
        ###
        sampleDegs = SCAN_DEGS / sampleCnt

        ###
        ### Only process received frames if the last plot update is complete.
        ###
        if not do_plot.is_set():
          for i in range(sampleCnt):
            #signalQuality = frame.parameters[5 + (i * 3)]
            distance = (frame.parameters[5 + (i * 3) + 1] << 8) + frame.parameters[5 + (i * 3) + 2]
            #scanSamplesSignalQuality.append(signalQuality)
            scanSamplesRange.append(distance * RANGE_SCALE)
            scanSamplesAngle.append(startAngle + (i * sampleDegs))
          if frameIndex == (SCAN_STEPS - 1):
            ###
            ### A complete 360deg of samples has been taken, plot them.
            ###
            buffer['bearings'] = np.array(scanSamplesAngle)
            buffer['ranges'] = np.array(scanSamplesRange)
            do_plot.set()

            ###
            ### Clear all the temporary buffers/values ready to take fresh data.
            ###
            scanSamplesAngle.clear()
            scanSamplesRange.clear()
            frameIndex = 0
          else:
            frameIndex = frameIndex + 1

def lidar_data_reader(buffer, stop_event, do_plot):
    status = 0
    checksum = 0
    lidarFrame = Delta2GFrame()
    while not stop_event.is_set():
        rx = lidarSerial.read(100)
        for by in rx:
            if status == 0:
                ###
                ### 1st frame byte: Frame Header
                ###
                lidarFrame.frameHeader = by
                if lidarFrame.frameHeader == FRAME_HEADER:
                    ###
                    ### Valid Header
                    ###
                    status = 1
                else:
                    print("ERROR: Frame Header Failed")
                ###
                ### Reset checksum, new frame start
                ###
                checksum = 0
            elif status == 1:
                ###
                ### 2nd frame byte: Frame Length MSB
                ###
                lidarFrame.frameLength = (by << 8)
                status = 2
            elif status == 2:
                ###
                ### 3rd frame byte: Frame Length LSB
                ###
                lidarFrame.frameLength += by
                status = 3
            elif status == 3:
                ###
                ### 4th frame byte: Protocol Version
                ###
                lidarFrame.protocolVersion = by
                if lidarFrame.protocolVersion == PROTOCOL_VERSION:
                    ###
                    ### Valid Protocol Version
                    ###
                    status = 4
                else:
                    print("ERROR: Frame Protocol Version Failed")
                    status = 0
            elif status == 4:
                ###
                ### 5th frame byte: Frame Type
                ###
                lidarFrame.frameType = by
                if lidarFrame.frameType == FRAME_TYPE:
                    ###
                    ### Valid Frame Type
                    ###
                    status = 5
                else:
                    print("ERROR: Frame Type Failed")
                    status = 0
            elif status == 5:
                ###
                ### 6th frame byte: Command Word
                ###
                lidarFrame.commandWord = by
                status = 6
            elif status == 6:
                ###
                ### 7th frame byte: Parameter Length MSB
                ###
                lidarFrame.parameterLength = (by << 8)
                status = 7
            elif status == 7:
                ###
                ### 8th frame byte: Parameter Length LSB
                ###
                lidarFrame.parameterLength += by
                lidarFrame.parameters.clear()
                status = 8
            elif status == 8:
                ###
                ### 9th+ frame bytes: Parameters
                ###
                lidarFrame.parameters.append(by)
                if len(lidarFrame.parameters) == lidarFrame.parameterLength:
                    ###
                    ### End of parameter frame bytes
                    ###
                    status = 9
            elif status == 9:
                ###
                ### N+1 frame byte: Checksum MSB
                ###
                lidarFrame.checksum = (by << 8)
                status = 10
            elif status == 10:
                ###
                ### N+2 frame byte: Checksum LSB
                ###
                lidarFrame.checksum += by

                ###
                ### End of frame reached
                ### Compare received and calculated frame checksum
                ###
                if lidarFrame.checksum == checksum:
                    ###
                    ### Checksum match: Valid frame
                    ###
                    LiDARFrameProcessing(lidarFrame, buffer, do_plot)
                else:
                    ###
                    ### Checksum missmatach: Invalid frame
                    ###
                    print("ERROR: Frame Checksum Failed");
                status = 0
            ###
            ### Calculate current frame checksum, excluding the checksum
            ###
            if status < 10:
                checksum = (checksum + by) % 0xFFFF

###
###  Function to initialize the plot area
###
def init_plot():
    fig, ax = plt.subplots(subplot_kw={'projection': 'polar'})
    plt.subplots_adjust(left=0.1, bottom=0.3)
    ax.set_title("Real-Time LiDAR Bearing and Distance Plot")
    ax.set_theta_zero_location('N')  # North on top (0 degrees)
    ax.set_theta_direction(-1)  # Clockwise direction
    ax.set_ylim(0, 4)
    line, = ax.plot([], [], marker='o', linestyle='-', color='b')
    return fig, ax, line

###
###  Function to update the plot with data from the shared buffer
###
def update_plot(buffer, stop_event, do_plot):
    ###
    ### Setup the plot
    ###
    fig, ax, line = init_plot()

    ax_slider = plt.axes([0.1, 0.15, 0.8, 0.03], facecolor='lightgoldenrodyellow')
    y_slider = Slider(
        ax=ax_slider,  # Slider axis
        label='Scale', # Label
        valmin=1,      # Min slider value
        valmax=10,     # Max slider value
        valinit=3,     # Initial value
        valstep=0.25   # Step size
    )

    def update(val):
        new_ymax = y_slider.val   # Get the slider's current value
        ax.set_ylim(0, new_ymax)  # Set the new Y-limit (radial limit)
        #fig.canvas.draw_idle()   # Redraw the figure to update the plot

    ###
    ###  Connect the slider to the update function
    ###
    y_slider.on_changed(update)

    ###
    ###  Create an axis for the button below the slider
    ###
    ax_button = plt.axes([0.4, 0.05, 0.2, 0.075])  # [left, bottom, width, height]

    ###
    ###  Create a button widget
    ###
    exit_button = Button(ax=ax_button, label='Exit', color='lightcoral', hovercolor='red')

    def exit_application(event):
        plt.close(fig)  # Close the figure and exit the application
        stop_event.set()  # Signal both processes to stop

    ###
    ###  Connect the button click event to the exit function
    ###
    exit_button.on_clicked(exit_application)

    ###
    ### Declare a plot update animation function.
    ###
    def animate(frame):
        if do_plot.is_set():
            ###
            ### Get the plot data.
            ###
            bearings = np.deg2rad(buffer['bearings'])
            distances = buffer['ranges']

            ###
            ### Update plot with new data.
            ###
            line.set_data(bearings, distances)

            ###
            ### Tell the frame decoder that the plot is ready for new data.
            ###
            do_plot.clear()
        return line,

    ###
    ### Register the animation callback function.
    ###
    ani = FuncAnimation(fig, animate, frames=None, blit=True, interval=250)
    plt.show()
    stop_event.set()  # Signal both processes to stop

###
### Main function to setup multiprocessing and plot updating
###
def main():
    ###
    ### Shared buffer for inter-process communication using a Manager dictionary
    ###
    manager = mp.Manager()
    buffer = manager.dict({'bearings': np.array([]), 'ranges': np.array([])})
    
    ###
    ### Event to signal process termination
    ###
    stop_event = mp.Event()
    
    ###
    ### Event to synchronise new data ready to be plotted.
    ###
    do_plot = mp.Event()
    
    ###
    ### Animation function with shared buffer access
    ###
    update_process = mp.Process(target=update_plot, args=(buffer, stop_event, do_plot))
    update_process.start()
    
    ###
    ### Start LiDAR data reader process
    ###
    reader_process = mp.Process(target=lidar_data_reader, args=(buffer, stop_event, do_plot))
    reader_process.start()
    
    ###
    ### Wait for the children processes
    ###
    update_process.join()
    reader_process.join()

###
### Run the main function
###
if __name__ == "__main__":
    main()
