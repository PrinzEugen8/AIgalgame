# Project Overview
- Game Title: RelaxRoomAI
- High-Level Concept: An interactive AI companion relaxation room game featuring "Hayaseyuuka" (a 3D VRM 1.0 character) with automated motion play, audio triggers, phone attachment, and touch interactions.
- Players: Single Player
- Inspiration / Reference Games: Character-focused relaxation and interaction simulators
- Tone / Art Direction: Stylized 3D Anime / Galgame
- Target Platform: Standalone Windows 64-bit
- Screen Orientation / Resolution: Landscape (1920x1080)
- Render Pipeline: Universal Render Pipeline (URP)

# Game Mechanics
## Core Gameplay Loop
Players interact with Hayaseyuuka in her apartment, triggering motions, audio dialogues, and viewing physics-based interactive responses. The system plays back predefined clips and dynamically adjusts joints using procedural rigging and target attachments (such as phones).

## Controls and Input Methods
Mouse drag and click interactions in the UI, and direct manipulation of targets/poles in the editor scene view to pose her and test new configurations.

# UI
There are custom in-game UI overlay canvases and custom Editor menus/tools to trigger setup, test clips, and preview attachments.

# Key Asset & Context
- **Character GameObject**: `Hayaseyuuka` in `Scene_01.unity`
  - Has VRM, Animator, and custom motion components.
  - Already has a `Rig 1` child GameObject (with a `Rig` component, currently empty).
  - Already has an `AIgalgame_IKTargets` child with targets: `LeftHand_Target`, `RightHand_Target`, `LeftFoot_Target`, `RightFoot_Target`, `LookAt_Target`, `LeftHand_Pole`, `RightHand_Pole`, `LeftFoot_Pole`, `RightFoot_Pole`.
- **Skeleton Bones**:
  - Left Arm: `upper_arm.L` -> `lower_arm.L` -> `Hand.L`
  - Right Arm: `upper_arm.R` -> `lower_arm.R` -> `Hand.R`
  - Left Leg: `upper_leg.L` -> `lower_leg.L` -> `foot.L`
  - Right Leg: `upper_leg.R` -> `lower_leg.R` -> `foot.R`
  - Head/Neck: `neck` -> `Head`

# Implementation Steps
We will write a automated setup Editor tool to handle the entire rigging structure and socket creation cleanly. This guarantees perfect wiring of the 20+ fields and ensures reproducibility.

### Step 1: Create `AIGalgameHayaseyuukaRigSetup.cs`
- **Description**: Create a custom Editor script `Assets/_AIgalgame/Editor/AIGalgameHayaseyuukaRigSetup.cs` which adds a menu item `Tools/AIgalgame/Rigging/Setup Hayaseyuuka Rig and Sockets`.
- **Assigned role**: developer
- **Dependencies**: None
- **Parallelizable**: No

### Step 2: Implement Setup Automation Logic
- **Description**: The script will:
  1. Find `Hayaseyuuka` in the active scene.
  2. Find or add `RigBuilder` to her root.
  3. Ensure `Rig 1` child has `Rig` component and clean up any existing children inside it to allow rebuilding safely.
  4. Create four `TwoBoneIKConstraint` objects under `Rig 1`:
     - **LeftArm_IK**: Root: `upper_arm.L`, Mid: `lower_arm.L`, Tip: `Hand.L`, Target: `LeftHand_Target`, Pole: `LeftHand_Pole`.
     - **RightArm_IK**: Root: `upper_arm.R`, Mid: `lower_arm.R`, Tip: `Hand.R`, Target: `RightHand_Target`, Pole: `RightHand_Pole`.
     - **LeftLeg_IK**: Root: `upper_leg.L`, Mid: `lower_leg.L`, Tip: `foot.L`, Target: `LeftFoot_Target`, Pole: `LeftFoot_Pole`.
     - **RightLeg_IK**: Root: `upper_leg.R`, Mid: `lower_leg.R`, Tip: `foot.R`, Target: `RightFoot_Target`, Pole: `RightFoot_Pole`.
  5. Create one `MultiAimConstraint` object under `Rig 1`:
     - **Head_LookAt**: Constrained: `Head` bone, Source: `LookAt_Target` with weight 1.0.
  6. Add `Rig 1` to the layers array of the `RigBuilder`.
  7. Create empty GameObjects `LeftHand_Socket` under `Hand.L` and `RightHand_Socket` under `Hand.R` at local position (0,0,0) and local rotation (0,0,0).
  8. Register the setup actions with the Undo system and save the scene.
- **Assigned role**: developer
- **Dependencies**: Step 1
- **Parallelizable**: No

### Step 3: Run the Automation Setup
- **Description**: Execute the menu command `Tools/AIgalgame/Rigging/Setup Hayaseyuuka Rig and Sockets` to automatically generate all rigging constraints and sockets in `Scene_01`.
- **Assigned role**: developer
- **Dependencies**: Step 2
- **Parallelizable**: No

### Step 4: Verify and Test Pose Rig and Hand Sockets
- **Description**: Verify correctness in the Unity Editor:
  1. Select `Hayaseyuuka` and check if the missing/null script warning on her root is resolved (or clean it up), and a `RigBuilder` is present with `Rig 1` listed in layers.
  2. Move the `LeftHand_Target` and `RightHand_Target` in Scene view to confirm her hands follow procedural IK.
  3. Select `Hand.L` and `Hand.R` and check that `LeftHand_Socket` and `RightHand_Socket` exist. Test dropping a dummy 3D object under these sockets to confirm they move correctly with her hands.
- **Assigned role**: developer
- **Dependencies**: Step 3
- **Parallelizable**: No

# Verification & Testing
1. **Scene Compilation & Build Checks**: Verify no C# compilation errors are generated.
2. **Editor Validation**: Select her IK target objects and drag them in the Scene View. Her limbs should smoothly track using the Two-Bone IK constraints.
3. **Socket Testing**: Place an item child (like a cube) under `LeftHand_Socket`/`RightHand_Socket` and confirm it moves along with the hand.
