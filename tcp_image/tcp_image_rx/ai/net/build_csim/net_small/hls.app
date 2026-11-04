<AutoPilot:project xmlns:AutoPilot="com.autoesl.autopilot.project" projectType="C/C++" name="net_small" ideType="classic" top="conv_layer">
    <files>
        <file name="E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_eltwise/src/eltwise_layer.cpp" sc="0" tb="false" cflags="-IE:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_eltwise/src" csimflags="" blackbox="false"/>
        <file name="E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src/conv_layer.cpp" sc="0" tb="false" cflags="-IE:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/unet_conv/src" csimflags="" blackbox="false"/>
        <file name="E:/fpga/my_project/Tcp_Image_Rx/ai/net/tb_network_dump.cpp" sc="0" tb="1" cflags="-std=c++14 -Wno-unknown-pragmas" csimflags="" blackbox="false"/>
    </files>
    <solutions>
        <solution name="solution1" status=""/>
    </solutions>
    <Simulation argv="E:/fpga/my_project/Tcp_Image_Rx/ai/net/vectors_small_alt">
        <SimFlow name="csim" setup="false" optimizeCompile="true" clean="false" ldflags="" mflags=""/>
    </Simulation>
</AutoPilot:project>
