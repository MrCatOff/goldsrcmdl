import argparse
import configparser
import os
import re
import shutil
import glob
import copy
from valve.QCParser import QCParser
from valve.SMDParser import SMDParser

parser = argparse.ArgumentParser(description="Combine multiple models into single model.")
parser.add_argument(
    "--compiler",
    type=str,
    default="bin/studiomdl.exe",
    help="StudioMDL Compiler (default: %(default)s)"
)
parser.add_argument(
    "--input-dir",
    type=str,
    default="storage/decompiled",
    help="Decompiled files (default: %(default)s)"
)
parser.add_argument(
    "--temp-dir",
    type=str,
    default="storage/temp",
    help="Temp directory (default: %(default)s)"
)
parser.add_argument(
    "--build-dir",
    type=str,
    default="storage/build",
    help="Build directory (default: %(default)s)"
)
parser.add_argument(
    "--output-mdl",
    type=str,
    default="v_combined_models.mdl",
    help="Combined Models name (default: %(default)s)"
)
parser.add_argument(
    "--output-qc",
    type=str,
    default="v_combined_models.qc",
    help="Combined QC File (default: %(default)s)"
)
parser.add_argument(
    "--output-ini",
    type=str,
    default="combined-models.ini",
    help="INI File (default: %(default)s)"
)
parser.add_argument(
    "--mode",
    type=str,
    default="v",
    help="Model worker mode (default: %(default)s, Allow: v, p, w)"
)


args = parser.parse_args()


class BoneManager:
    def __init__(self, config_path='bones-config.ini'):
        self.patterns = None
        self.prefixes = None
        self.explicit_bones = None
        self.config_path = config_path
        self.config = configparser.ConfigParser()

        # Define defaults in case the file doesn't exist
        self.defaults = {
            'explicit_bones': "universal_root, bone_lefthand, bone_righthand, bone_se_hand-l, bone_se_hand-r, bone_l_upper, bone_r_upper",
            'prefixes': "bip01",
            'patterns': r"^bone\d+$"
        }

        self.load_or_create_config()

    def load_or_create_config(self):
        """Reads config; if missing, creates it with default values."""
        if not os.path.exists(self.config_path):
            self.config['BoneSettings'] = self.defaults
            with open(self.config_path, 'w') as configfile:
                self.config.write(configfile)
            print(f"Created default config at: {self.config_path}")
        else:
            self.config.read(self.config_path)

        # Cache values for performance
        section = self.config['BoneSettings']
        self.explicit_bones = set(name.strip().lower() for name in section.get('explicit_bones', '').split(','))
        self.prefixes = tuple(p.strip().lower() for p in section.get('prefixes', '').split(','))

        # Pre-compile regex patterns
        raw_patterns = section.get('patterns', '').split(',')
        self.patterns = [re.compile(p.strip().lower()) for p in raw_patterns if p.strip()]

    def is_shared_bone(self, bone_name):
        """The core logic checking against the loaded config."""
        low = bone_name.lower()

        # Check explicit list
        if low in self.explicit_bones:
            return True

        # Check prefixes
        if low.startswith(self.prefixes):
            return True

        # Check regex patterns
        if any(pattern.match(low) for pattern in self.patterns):
            return True

        return False


class SequenceNormalizer:
    def __init__(self, config_path='sequence-config.ini'):
        self.config_path = config_path
        self.config = configparser.ConfigParser()

        # Mapping: { "OldName": "CorrectName" }
        self.fix_map = {}

        # Default data: "CorrectName": "Comma, Separated, Aliases"
        self.defaults = {
            'idle': ":LARS-DAY[BR]EAKER:, LARS-DAY[BR]EAKER, LARS-DAYBREAKER",
        }

        self.load_or_create_config()

    def load_or_create_config(self):
        """Reads config; creates it with defaults if missing."""
        if not os.path.exists(self.config_path):
            self.config['TextFixes'] = self.defaults
            with open(self.config_path, 'w', encoding='utf-8') as f:
                self.config.write(f)
        else:
            self.config.read(self.config_path, encoding='utf-8')

        # Load the section, falling back to defaults if section is missing
        section = self.config['TextFixes'] if 'TextFixes' in self.config else self.defaults

        # Clear map and rebuild reverse lookup
        self.fix_map = {}
        for correct_name, aliases_str in section.items():
            # Split aliases and map each one back to the key (correct_name)
            aliases = [a.strip().lower() for a in aliases_str.split(',') if a.strip()]
            for alias in aliases:
                self.fix_map[alias] = correct_name

    def normalize(self, text):
        """
        Returns the corrected version of the string if it's an alias.
        Otherwise, returns the original string.
        """
        if not text:
            return text

        lookup = text.strip().lower()
        # Return the mapped value if found, else the original text
        return self.fix_map.get(lookup, text)


class MDLCombiner:
    def __init__(self, configuration):
        self.compiler = configuration.compiler
        self.input_dir = configuration.input_dir
        self.build_dir = configuration.build_dir
        self.temp_dir = configuration.temp_dir
        self.output_mdl = configuration.output_mdl
        self.output_qc = configuration.output_qc
        self.output_ini = configuration.output_ini
        self.mode = configuration.mode
        self.bone_manager = BoneManager()
        self.sequence_normalizer = SequenceNormalizer()
        self.model_configuration = {
            "GENERAL": {
                "model": self.output_mdl,
            }
        }

    def validate(self):
        if not os.path.exists(self.compiler):
            raise FileNotFoundError(f"Compiler missing: {self.compiler}")
        if not os.path.exists(self.input_dir):
            raise FileNotFoundError(f"Input directory missing: {self.input_dir}")
        if not os.path.exists(self.temp_dir):
            raise FileNotFoundError(f"Temp directory missing: {self.input_dir}")

    @staticmethod
    def normalize_and_merge_hitboxes(master_qc, list_of_child_qcs):
        unique_hitboxes = {}
        for qc in list_of_child_qcs:
            for hb in qc.hitboxes:
                # hb format in your parser is: [group, bone_name, minX, minY, minZ, maxX, maxY, maxZ]
                bone_name = hb[1]
                bone_lower = bone_name.lower()

                if bone_lower not in unique_hitboxes:
                    unique_hitboxes[bone_lower] = list(hb)

        normalized_list = []
        for bone_lower, hb in unique_hitboxes.items():
            # Apply logic to categorize the hitbox group based on the bone name
            if "head" in bone_lower or "neck" in bone_lower:
                hb[0] = "1"  # Head
            elif "spine" in bone_lower or "chest" in bone_lower:
                hb[0] = "2"  # Chest
            elif "pelvis" in bone_lower or "stomach" in bone_lower or "hip" in bone_lower:
                hb[0] = "3"  # Stomach/Pelvis
            elif "left" in bone_lower or "_l" in bone_lower or "l_" in bone_lower:
                if "arm" in bone_lower or "hand" in bone_lower or "shoulder" in bone_lower:
                    hb[0] = "4"  # Left Arm
                elif "leg" in bone_lower or "foot" in bone_lower or "thigh" in bone_lower or "calf" in bone_lower:
                    hb[0] = "6"  # Left Leg (Standard Source Engine mapping)
            elif "right" in bone_lower or "_r" in bone_lower or "r_" in bone_lower:
                if "arm" in bone_lower or "hand" in bone_lower or "shoulder" in bone_lower:
                    hb[0] = "5"  # Right Arm
                elif "leg" in bone_lower or "foot" in bone_lower or "thigh" in bone_lower or "calf" in bone_lower:
                    hb[0] = "7"  # Right Leg

            else:
                hb[0] = "0"  # Generic fallback

            normalized_list.append(hb)

        master_qc.hitboxes = normalized_list

    @staticmethod
    def normalize_and_merge_attachments(master_qc, list_of_child_qcs):
        """
        Extracts attachments from multiple QCParser instances, deduplicates them by
        attachment name, and adds them to the master QC.
        """
        unique_attachments = {}

        for qc in list_of_child_qcs:
            for att in qc.attachments:
                if len(att) > 0:
                    att_name = att[0]
                    att_lower = att_name.lower()

                    if att_lower not in unique_attachments:
                        unique_attachments[att_lower] = list(att)

        master_qc.attachments = list(unique_attachments.values())

    def execute(self):
        self.validate()
        if os.path.exists(self.temp_dir): shutil.rmtree(self.temp_dir)
        os.makedirs(self.temp_dir)

        weapon_folders = [f for f in os.listdir(self.input_dir) if os.path.isdir(os.path.join(self.input_dir, f))]

        if not weapon_folders:
            print(f"Folder {self.input_dir} does not contain any weapons.")
            return

        print(f"Found {len(weapon_folders)} weapon folders.\n")

        seq_last_idx = 0
        combined_qc = QCParser("", False, self.mode)
        combined_qc.modelname = self.output_mdl
        combined_qc.cd = "."
        combined_qc.cdtexture = "."
        combined_qc.scale = 1.0
        combined_qc.other_commands.append(['$cliptotextures'])

        models_qc = []

        for index, folder_name in enumerate(weapon_folders):
            print(f"Processing folder: {folder_name}")
            print(f"Folder bones prefix: _TYPE{index}")
            weapon_name = folder_name.replace('v_', '')
            weapon_entity = f"weapon_{weapon_name}"
            src_path = os.path.join(self.input_dir, folder_name)
            temp_path = os.path.join(self.temp_dir, folder_name)
            sequences_path = os.path.join(self.temp_dir, folder_name, 'sequences')
            qc_files = glob.glob(os.path.join(src_path, "*.qc"))
            if not qc_files:
                print(f"Folder {src_path} does not contain any QC files and be ignored.")
                continue

            os.makedirs(temp_path)
            os.makedirs(sequences_path)

            if weapon_name not in self.model_configuration:
                self.model_configuration[weapon_entity] = {}
                self.model_configuration["GENERAL"][weapon_entity] = f"{folder_name}.mdl"

            for qc_file in qc_files:
                print(f"Processing QC file: {qc_file}")
                qc = QCParser(qc_file, True, self.mode)
                sequences = []
                for i, sequence in enumerate(qc.sequences):
                    normalize_name = self.sequence_normalizer.normalize(sequence['name'])
                    sequence['name'] = f"{normalize_name}_{weapon_name}_{i}"

                    smdfiles = []
                    for j, smd_path in enumerate(sequence['smdfiles']):
                        smd_name = f"{normalize_name}_{j}"
                        smd_file_name = f"{smd_name}.smd"
                        smd = SMDParser(os.path.join(src_path, str(smd_path).replace('\\', '/') + '.smd'), True, self.mode)

                        with open(os.path.join(sequences_path, smd_file_name), 'w') as smd_file:
                            smd_file.write(str(smd))
                            smdfiles.append(f"sequences/{smd_name}")
                    sequence['smdfiles'] = smdfiles
                    sequences.append(sequence)

                    combined_sequence = copy.copy(sequence)
                    combined_sequence['smdfiles'] = [folder_name + "/" + item for item in sequence['smdfiles']]
                    combined_qc.sequences.append(combined_sequence)

                    self.model_configuration[weapon_entity][normalize_name] = seq_last_idx
                    seq_last_idx += 1
                qc.sequences = sequences
                textures = []

                # Add parse body because model can be with body or bodygroups
                # {name: "", smd: ""}
                for i, body in enumerate(qc.body):
                    raise NotImplementedError('Body not implemented yet.')

                for i, body_group in enumerate(qc.bodygroups):
                    models = []
                    for j, model in enumerate(body_group['models']):
                        if not model['smd']: continue
                        smd_name = f"{weapon_name}_{i}_{j}"
                        smd_file = f"{smd_name}.smd"
                        smd = SMDParser(os.path.join(src_path, str(model['smd']).replace('\\', '/') + '.smd'), True, self.mode)

                        triangles = []
                        for triangle in smd.triangles:
                            if triangle['texture'] not in textures:
                                textures.append(triangle['texture'])
                            triangle['texture'] = f"texture_{weapon_name}_{textures.index(triangle['texture'])}.BMP"
                            triangles.append(triangle)
                        smd.triangles = triangles

                        models.append({
                            'type': model['type'],
                            'smd': smd_name
                        })

                        with open(os.path.join(temp_path, smd_file), 'w') as smd_file:
                            smd_file.write(str(smd))

                    qc.bodygroups[i]['models'] = models

                for i, texture in enumerate(textures):
                    texture_name = f"texture_{weapon_name}_{i}.BMP"
                    shutil.copy(os.path.join(src_path, texture), os.path.join(temp_path, texture_name))
                    shutil.copy(os.path.join(src_path, texture), os.path.join(self.temp_dir, texture_name))

                with open(os.path.join(temp_path, f"{weapon_name}.qc"), 'w') as weapon_qc:
                    qc.filepath = os.path.join(temp_path, f"{weapon_name}.qc")
                    weapon_qc.write(str(qc))

                qc.patch_bones(f"_TYPE{index}", self.bone_manager.is_shared_bone)
                models_qc.append(qc)

            for smd_path in glob.glob(os.path.join(temp_path, "**/*.smd"), recursive=True):
                smd = SMDParser(smd_path, True, self.mode)
                smd.patch_bones(f"_TYPE{index}", self.bone_manager.is_shared_bone)
                with open(smd_path, "w") as f:
                    f.write(str(smd))

        max_parts = max([len(d.bodygroups) for d in models_qc])
        for i in range(max_parts):
            combined_qc.bodygroups.append({
                "name": f"body_group_{i}",
                "models": [],
            })

        for qc in models_qc:
            base_name = os.path.basename(os.path.dirname(qc.filepath))

            for index, body_group in enumerate(combined_qc.bodygroups):
                if 0 <= index < len(qc.bodygroups):
                    for i, model in enumerate(qc.bodygroups[index]['models']):
                        if "smd" in model:
                            model['smd'] = f"{base_name}/{model['smd']}"
                        combined_qc.bodygroups[index]['models'].append(model)
                else:
                    combined_qc.bodygroups[index]['models'].append({"type": "blank"})

        MDLCombiner.normalize_and_merge_hitboxes(combined_qc, models_qc)
        MDLCombiner.normalize_and_merge_attachments(combined_qc, models_qc)

        with open(os.path.join(self.temp_dir, self.output_qc), 'w') as main_qc:
            main_qc.write(str(combined_qc))
        self.create_ini()

    def create_ini(self):
        config = configparser.ConfigParser()
        config.read_dict(self.model_configuration)
        with open(os.path.join(self.build_dir, self.output_ini), 'w') as config_file:
            config.write(config_file)


if __name__ == '__main__':
    combiner = MDLCombiner(args)
    combiner.execute()
