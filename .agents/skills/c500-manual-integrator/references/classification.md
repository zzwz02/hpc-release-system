# Classification Reference

Use these categories as defaults when merging release-system manual artifacts into the C500 RST manuals and when classifying release-note rows. Prefer the current RST structure when it is already accurate; update names when the artifact content makes a better category clear.

Release-note classifications must use these same category names so `新增特性及变更` and `发布列表` stay consistent with `HPC_Manual_CN.rst` chapter 10 and `C500_AI4SciUserGuide_CN.rst` chapters 5 and 6.

## HPC Chapter 10

- 基准测试: HPL, HPCG
- 材料科学计算: ABACUS, GAMESS, GPAW, LAMMPS when used for materials workflows, Quantum ESPRESSO, Siesta
- 分子模拟: GROMACS, Amber, NAMD, OpenMM
- 数值计算库与求解器: hypre, PETSc, SuperLU, Trilinos, MAGMA
- HPC框架与工具: Kokkos, RAJA, Charm++, HPX, Legion, Slurm, HPC SDK基础镜像
- CFD与流体模拟: OpenFOAM, SU2, NekRS, ExaFOAM
- 生命科学计算: NWChem, VASP-related bio workflows only when artifact says so
- 医学影像: MONAI when placed in HPC docs as imaging compute
- 地球系统模拟: E3SM, CESM, WRF when coupled earth-system context is explicit
- 海洋环流模拟: MITgcm, MOM6, ROMS
- 天气预报模拟: WRF, ICON, MPAS, FV3
- 物理仿真: WarpX, AMReX applications, PIConGPU
- 量子与高能物理计算: QUDA, Chroma, Grid, Geant4
- 信号处理: cuSignal-equivalent libraries, FFT/signal apps
- 密码学与安全计算: sppark or zero-knowledge proof tools
- 可视化: ParaView, VTK-m

## AI4Sci Chapter 5

Use chapter 5 for frameworks, libraries, and AI4Sci infrastructure:

- AI for Science框架: DeepXDE, FTorch, PaddleScience, PhysicsNeMo
- 图学习框架与AI工具: DGL, PyG
- Add a new subgroup only when an artifact is clearly neither a science framework nor a graph/AI tooling library.

## AI4Sci Chapter 6

Use chapter 6 for models and group by discipline:

- 气象与气候模型: ai-models, Earth-2, NeuralGCM, NowcastNet, Pangu-Weather, GraphCast, FourCastNet, FuXi, FengWu, AIFS, Aurora
- 蛋白质与生物分子模型: AlphaFold 3, BindCraft, ESM3, HelixFold3, OpenFold, ProtTrans, rc-foundry
- 材料科学模型: ALIGNN, AVIARY, DARWIN, EquiformerV2, MACE, MatRIS, MatterGen, MatterSim
- 分子模型: Boltz-2, DiffDock and ligand/protein docking or molecular generation models
- 基因与组学模型: Cell2location, PePPER and sequencing/genomics models
- 医学影像模型: MONAI and medical imaging AI models
- CFD模型: DeepCFD and fluid simulation neural models

When an APP could fit multiple categories, classify by the workflow emphasized in the release artifact, not by the upstream project’s broad scope.

## Common Category Name Normalizations

Use these normalizations when older release notes, DB exports, or generated drafts use legacy category names:

- `HPC基准测试` -> `基准测试`
- `HPC框架/工具` -> `HPC框架与工具`
- `CFD/流体模拟` -> `CFD与流体模拟`
- `数值计算库/求解器` -> `数值计算库与求解器`
- `量子/高能物理计算` -> `量子与高能物理计算`
- `密码学/安全计算` -> `密码学与安全计算`
- `AI框架/工具` -> `图学习框架与AI工具`
- `气象/气候模型` -> `气象与气候模型`
- `蛋白质/生物分子模型` -> `蛋白质与生物分子模型`
- `基因/组学模型` -> `基因与组学模型`
