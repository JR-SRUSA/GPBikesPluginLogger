GPBikes Plugin Logger
=====================

Simple logger using GPBikes Plugin structure to log to a binary file that can then be converted into a .csv

To Compile, from Developer Power Shell (install Visual Studio Build Tools NOT VSC or Visual Studio Community, you will need to scroll down); https://visualstudio.microsoft.com/downloads/), "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Visual Studio 2026\Visual Studio Tools\VC\x64 Native Tools Command Prompt for VS.lnk":

    > cd C:\Users\G04590\projects\GPBikesPluginLogger
    > cl /LD /O2 gpb_binary_recorder.c /Fe:gpb_binary_recorder.dll
    > copy /Y gpb_binary_recorder.dll gpb_binary_recorder.dlo


Move to GPB Plugin directory:

    > copy /Y gpb_binary_recorder.dlo "C:\Program Files (x86)\Steam\steamapps\common\GP Bikes\plugins\gpb_binary_recorder.dlo"
    > copy /Y gpb_binary_recorder.ini "C:\Program Files (x86)\Steam\steamapps\common\GP Bikes\plugins\gpb_binary_recorder.ini"

