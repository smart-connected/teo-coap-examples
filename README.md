# README #

Example application to connect 1-wire temperature sensor to Teo

### Preliminary howto ###

1. Install Raspbian on SD card
2. Insert card into RaspberryPi, plugin Ethernet cable to Raspberry Pi, power up
3. Log in to your RaspberryPi using SSH
4. Verify connectivity (ping sastrion.com)
5. Install software:

        sudo apt-get install python-dev
        sudo apt-get install python-pip
        sudo pip install txthings
        sudo pip install msgpack-python
        sudo pip install w1thermsensor


6. Download teo_coap.py or clone this repository
7. Run:

    There are two methods to run client:

    a) As a python script (good for testing):

        python teo_coap.py

    b) With twistd daemonizing tool:

        twistd -y teo_coap.py