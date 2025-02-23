import subprocess
import time

def start_listener():
    print("Starting listener.py...")
    listener_process = subprocess.Popen(["python", "webhook/listener.py"])
    time.sleep(20)  # Wait for listener to populate the database (adjust as needed)
    return listener_process

def start_sender():
    print("Starting sender.py...")
    sender_process = subprocess.Popen(["python", "sighook/sender.py"])
    return sender_process

if __name__ == "__main__":
    # Start listener
    listener_process = start_listener()

    # Start sender
    sender_process = start_sender()

    # Monitor both processes
    try:
        listener_process.wait()
        sender_process.wait()
    except KeyboardInterrupt:
        print("Stopping processes...")
        listener_process.terminate()
        sender_process.terminate()
