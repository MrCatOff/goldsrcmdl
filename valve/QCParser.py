import shlex
import os
import copy


class QCParser:
    def __init__(self, filepath=None, parse = False, mode = "v"):
        self.filepath = filepath
        self.mode = mode
        self.modelname = ""
        self.cd = ""
        self.cdtexture = ""
        self.scale = 1.0
        self.body = []
        self.bodygroups = []
        self.sequences = []
        self.attachments = []
        self.hitboxes = []
        self.other_commands = []
        if parse:
            self.parse()

    def patch_bones(self, bone_suffix, is_shared_bone_func, conflict_set=None, patched_bones=None):
        """
        Applies the weapon suffix to any non-shared bones in the QC file
        (specifically in hitboxes and attachments) so they match the patched SMDs.
        """

        if conflict_set is None:
            conflict_set = set()

        if patched_bones is None:
            patched_bones = dict()

        # Patch Attachments (Format: [name, bone_name, x, y, z, ...])
        for att in self.attachments:
            if len(att) > 1:
                bone_name = att[1]
                if bone_name in patched_bones:
                    att[1] = patched_bones[bone_name]

        # Patch Hitboxes (Format: [group, bone_name, minX, minY, minZ, maxX, maxY, maxZ])
        for hb in self.hitboxes:
            if len(hb) > 1:
                bone_name = hb[1]
                if bone_name in patched_bones:
                    hb[1] = patched_bones[bone_name]

    def parse(self):
        if not self.filepath:
            return

        try:
            with open(self.filepath, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            print(f"File not found: {self.filepath}")
            return

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Strip out comments
            if "//" in line:
                line = line.split("//")[0].strip()

            if not line:
                i += 1
                continue

            # Safely split strings preserving quotes
            tokens = shlex.split(line)
            if not tokens:
                i += 1
                continue

            cmd = tokens[0].lower()

            if cmd == "$modelname":
                self.modelname = tokens[1]
            elif cmd == "$cd":
                self.cd = tokens[1]
            elif cmd == "$cdtexture":
                self.cdtexture = tokens[1]
            elif cmd == "$scale":
                self.scale = float(tokens[1])
            elif cmd == "$body":
                self.body.append({"name": tokens[1], "smd": tokens[2]})
            elif cmd == "$bodygroup":
                bg_name = tokens[1]
                bg_models = []
                i += 1
                # Parse the multi-line block for bodygroups
                while i < len(lines):
                    bg_line = lines[i].strip()
                    if "//" in bg_line:
                        bg_line = bg_line.split("//")[0].strip()
                    if not bg_line or bg_line == "{":
                        i += 1
                        continue
                    if bg_line == "}":
                        break

                    bg_tokens = shlex.split(bg_line)
                    if bg_tokens[0].lower() == "studio":
                        bg_models.append({"type": "studio", "smd": bg_tokens[1]})
                    elif bg_tokens[0].lower() == "blank":
                        bg_models.append({"type": "blank"})
                    i += 1
                self.bodygroups.append({"name": bg_name, "models": bg_models})
            elif cmd == "$sequence":
                # Captures name, SMD file, and any trailing parameters (fps, loop, etc.)
                # Initialize a complex sequence dictionary
                seq = {
                    "name": tokens[1],
                    "smdfiles": [],
                    "events": [],
                    "options": [],
                    "is_block": False
                }

                tokens_after_name = tokens[2:]

                # Check if block starts on this line
                if "{" in tokens_after_name:
                    seq["is_block"] = True
                    tokens_after_name.remove("{")

                # Check if block starts on the next line
                if not seq["is_block"] and (i + 1 < len(lines)) and lines[i + 1].strip().startswith("{"):
                    seq["is_block"] = True
                    i += 1  # Consume the '{' line

                if not seq["is_block"]:
                    # SINGLE-LINE sequence (e.g. $sequence look_idle "new_idle2" loop fps 14 ACT_IDLE 2)
                    if len(tokens_after_name) > 0:
                        seq["smdfiles"].append(tokens_after_name[0])
                        seq["options"] = tokens_after_name[1:]
                else:
                    # MULTI-LINE BLOCK sequence
                    i += 1
                    while i < len(lines):
                        block_line = lines[i].strip()
                        if "//" in block_line: block_line = block_line.split("//")[0].strip()
                        if not block_line: i += 1; continue

                        if block_line == "}":
                            break

                        # Handle inline events { event 5001 1 "21" }
                        if block_line.startswith("{") and "event" in block_line:
                            event_data = block_line.strip("{ }").strip()
                            seq["events"].append(event_data)
                            i += 1
                            continue

                        sub_toks = shlex.split(block_line)
                        if not sub_toks: i += 1; continue

                        sub_cmd = sub_toks[0].lower()
                        # Sequence specific option keywords
                        if sub_cmd in ["fps", "loop", "blend", "node", "transition", "r_start", "r_loop", "walkframe",
                                       "pivot", "activity"]:
                            seq["options"].append(block_line)  # Keep exact string to preserve internal quotes
                        elif sub_cmd == "event":
                            seq["events"].append(block_line)
                        elif sub_cmd.startswith("act_"):
                            seq["options"].append(block_line)
                        else:
                            # If it's not a known option, it's an SMD file path
                            seq["smdfiles"].append(sub_toks[0])

                        i += 1

                self.sequences.append(seq)
            elif cmd == "$attachment":
                self.attachments.append(tokens[1:])
            elif cmd == "$hbox":
                self.hitboxes.append(tokens[1:])
            else:
                self.other_commands.append(tokens)

            i += 1

    def __str__(self):
        """Returns the parsed data formatted as a standard GoldSrc QC file."""
        lines = []

        # 1. Header Information
        if self.modelname: lines.append(f'$modelname "{self.modelname}"')
        if self.cd: lines.append(f'$cd "{self.cd}"')
        if self.cdtexture: lines.append(f'$cdtexture "{self.cdtexture}"')
        if self.scale != 1.0: lines.append(f'$scale {self.scale}')

        # 2. Miscellaneous commands ($bbox, $eyeposition, $flags)
        for cmd in self.other_commands:
            cmd_name = cmd[0]
            # Quote arguments only if they contain spaces
            args_str = " ".join(f'"{a}"' if " " in a else a for a in cmd[1:])
            lines.append(f"{cmd_name} {args_str}".strip())

        lines.append("")  # Blank line separator

        # 3. Body and Bodygroups
        for b in self.body:
            lines.append(f'$body "{b["name"]}" "{b["smd"]}"')

        for bg in self.bodygroups:
            lines.append(f'$bodygroup "{bg["name"]}"\n{{')
            for model in bg["models"]:
                if model["type"] == "studio":
                    lines.append(f'\tstudio "{model["smd"]}"')
                elif model["type"] == "blank":
                    lines.append('\tblank')
            lines.append("}")

        lines.append("")

        # 4. Attachments
        for att in self.attachments:
            # Quote the bone name (which is usually index 1)
            formatted_args = [att[0], f'"{att[1]}"'] + att[2:]
            lines.append(f'$attachment {" ".join(formatted_args)}')

        lines.append("")

        # 5. Hitboxes
        for hb in self.hitboxes:
            # Quote the bone name (index 1)
            formatted_args = [hb[0], f'"{hb[1]}"'] + hb[2:]
            lines.append(f'$hbox {" ".join(formatted_args)}')

        lines.append("")

        # 6. Sequences
        for seq in self.sequences:
            if not seq.get("is_block"):
                # Reconstruct single-line sequence
                opts_str = " ".join(seq["options"])
                smd = seq["smdfiles"][0] if seq["smdfiles"] else ""
                lines.append(f'$sequence "{seq["name"]}" "{smd}" {opts_str}'.strip())
            else:
                # Reconstruct multi-line block sequence
                lines.append(f'$sequence "{seq["name"]}" {{')

                # Add SMD files (Handles multiple files for blends)
                for smd in seq["smdfiles"]:
                    lines.append(f'\t"{smd}"')

                # Add options (fps, blend, loop)
                for opt in seq["options"]:
                    lines.append(f'\t{opt}')

                # Add events
                for evt in seq["events"]:
                    lines.append(f'\t{{ {evt} }}')

                lines.append("}\n")

        return "\n".join(lines).strip()

    @staticmethod
    def merge(master_model_name, list_of_qcs):
        """
        Creates a single unified QCParser by merging multiple child QC files.
        Automatically fixes sequence/bodygroup filepaths, pads bodygroups,
        and normalizes hitboxes and attachments.
        """
        master = QCParser()
        master.modelname = master_model_name
        master.cd = "."
        master.cdtexture = "."
        master.scale = 1.0
        master.other_commands = [['$cliptotextures']]

        unique_hitboxes = {}
        unique_attachments = {}

        # 1. Determine max bodygroups for padding
        max_parts = max([len(qc.bodygroups) for qc in list_of_qcs], default=0)
        for i in range(max_parts):
            master.bodygroups.append({
                "name": f"body_group_{i}",
                "models": [],
            })

        for qc in list_of_qcs:
            # Get the folder name (e.g., "v_ak47") from the filepath to prefix SMDs
            base_name = os.path.basename(os.path.dirname(qc.filepath)) if qc.filepath else ""
            prefix = f"{base_name}/" if base_name else ""

            # 2. Merge Sequences (Fixing paths to point to correct sub-directories)
            for seq in qc.sequences:
                new_seq = copy.deepcopy(seq)
                new_seq['smdfiles'] = [f"{prefix}{smd}" for smd in new_seq['smdfiles']]
                master.sequences.append(new_seq)

            # 3. Merge Bodygroups (With blank padding for missing parts)
            for index, master_bg in enumerate(master.bodygroups):
                if index < len(qc.bodygroups):
                    for model in qc.bodygroups[index]['models']:
                        new_model = copy.deepcopy(model)
                        if "smd" in new_model:
                            new_model['smd'] = f"{prefix}{new_model['smd']}"
                        master_bg['models'].append(new_model)
                else:
                    master_bg['models'].append({"type": "blank"})

            # 4. Collect Hitboxes (Deduplicate)
            for hb in qc.hitboxes:
                if len(hb) > 1:
                    bone_lower = hb[1].lower()
                    if bone_lower not in unique_hitboxes:
                        unique_hitboxes[bone_lower] = list(hb)

            # 5. Collect Attachments (Deduplicate)
            for att in qc.attachments:
                if len(att) > 0:
                    att_lower = att[0].lower()
                    if att_lower not in unique_attachments:
                        unique_attachments[att_lower] = list(att)

        # 6. Normalize Hitbox Bone Mappings
        for bone_lower, hb in unique_hitboxes.items():
            if "head" in bone_lower or "neck" in bone_lower:
                hb[0] = "1"
            elif "spine" in bone_lower or "chest" in bone_lower:
                hb[0] = "2"
            elif "pelvis" in bone_lower or "stomach" in bone_lower or "hip" in bone_lower:
                hb[0] = "3"
            elif "left" in bone_lower or "_l" in bone_lower or "l_" in bone_lower:
                hb[0] = "4" if any(x in bone_lower for x in ["arm", "hand", "shoulder"]) else "6"
            elif "right" in bone_lower or "_r" in bone_lower or "r_" in bone_lower:
                hb[0] = "5" if any(x in bone_lower for x in ["arm", "hand", "shoulder"]) else "7"
            else:
                hb[0] = "0"
            master.hitboxes.append(hb)

        # 7. Apply Unique Attachments
        master.attachments = list(unique_attachments.values())

        return master
