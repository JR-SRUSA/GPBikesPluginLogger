GPBikes Plugin Logger
=====================

Simple logger using GPBikes Plugin structure to log to a binary file that can then be converted into a .csv

To Compile, from Developer Power Shell (install Visual Studio Build Tools NOT VSC or Visual Studio Community, you will need to scroll down); https://visualstudio.microsoft.com/downloads/):

    > cl /LD /O2 gpb_binary_recorder.c /Fe:gpb_binary_recorder.dll
    > copy gpb_binary_recorder.dll gpb_binary_recorder.dlo

Move to GPB Plugin directory:

    gpb_binary_recorder.dlo
    gpb_binary_recorder.ini

