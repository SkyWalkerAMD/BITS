"""Synthetic console data only; imports no production modules."""
OVERVIEW = '''== GenuineIntel fam6  Per-socket Overview ==
  S0  Temp Max 24°C  TjMax 94°C  VCCIN 1.83 V  VID 0.9109 V
      Core 3300 MHz  Mesh 1400 MHz
      DRAM 4000 MT/s  4 DIMMs  Used 2.3/256 GB 0.9%  Mem Max 33°C
      Pkg 108.0 W  DRAM 1.8 W  PC2 0%  PC6 0%  PSU In 490.0 W  load sharing
== CPU ==
  S0  Intel(R) Xeon(R) w7-2495X  24C/24T  Base 2500 MHz
== Per-core Overview ==
  Core Freq Temp VID C0 C6 IRQ
  core0 3300 24°C 0.9109 100% 0% 1002
'''
INFO = '''== Platform ==
  Secure Boot Disabled  Lockdown none  OC Lock Disabled  x2APIC On  HT Off  NUMA 1
  IOMMU VT-d off
== CPU ==
  S0  Intel(R) Xeon(R) w7-2495X  24C/24T  fam6 model 143 stepping 8  ucode 0x2b000639
      Base 25x 2500 MHz  Max-Eff 8x  Min 5x
      Programmable: turbo-ratio yes  TDP-limit yes  TjMax-offset yes
== Turbo Ratio Limits ==
      <=2C 48x  <=4C 47x  <=6C 44x  <=10C 43x  <=14C 40x  <=18C 36x  <=20C 35x  <=24C 33x
== Thermal ==
  S0  TjMax 94°C  TCC/PROCHOT offset 0°C
== Power Limits ==
  S0  PL1 225.0 W Enabled 32.000 s  PL2 270.0 W Enabled 0.012 s
        Package: TDP 225.0 W
== Power Supplies ==
  Wall 500.0 W total
  PSU1_1 260.0 W
  PSU1_2 240.0 W
  Arrangement load sharing
== Memory ==
  DIMM           Part Number     Speed       JEDEC       VDDQ     Size     Temp
  CPU0_DIMM_A1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    30°C
  CPU0_DIMM_B1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    31°C
  CPU0_DIMM_E1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    32°C
  CPU0_DIMM_F1   HMCG94AHBRA480N 4000 MT/s   6400 MT/s   1.14 V   64 GB    33°C
== Memory Timings ==
  S0  Primary     32-32-31-65  tCWL 30
      Refresh     tRFC 160  tREFI 3900
      Secondary   tRTP 16  tFAW 28  tRRD_S 6  tRRD_L 8  tRCD_WR 32  tRAStoCAS 96
== Cache ==
     L1d 48K  L1i 32K  L2 2048K  L3 46080K
'''


