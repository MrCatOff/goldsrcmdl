import re
from collections import defaultdict


class SMDParser:
    def __init__(self, filepath, parse = False):
        self.filepath = filepath
        self.nodes = []
        self.skeleton = {}
        self.triangles = []
        if parse:
            self.parse()

    def parse(self):
        with open(self.filepath, 'r') as f:
            lines = f.readlines()

        current_section = None
        current_time = None

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Section headers
            if line == "nodes":
                current_section = "nodes"
            elif line == "skeleton":
                current_section = "skeleton"
            elif line == "triangles":
                current_section = "triangles"
            elif line == "end":
                current_section = None

            # Parsing logic based on current section
            elif current_section == "nodes":
                # Format: ID "Name" ParentID
                match = re.match(r'(\d+)\s+"([^"]+)"\s+(-?\d+)', line)
                if match:
                    self.nodes.append({
                        "id": int(match.group(1)),
                        "name": match.group(2),
                        "parent": int(match.group(3))
                    })

            elif current_section == "skeleton":
                if line.startswith("time"):
                    current_time = int(line.split()[1])
                    self.skeleton[current_time] = []
                else:
                    # Format: ID PosX PosY PosZ RotX RotY RotZ
                    parts = line.split()
                    if len(parts) == 7:
                        self.skeleton[current_time].append({
                            "bone_id": int(parts[0]),
                            "pos": [float(x) for x in parts[1:4]],
                            "rot": [float(x) for x in parts[4:7]]
                        })

            elif current_section == "triangles":
                # First line is the material/texture name
                texture = line
                vertices = []
                # Next 3 lines are the vertices of the triangle
                for _ in range(3):
                    i += 1
                    v_parts = lines[i].strip().split()
                    vertices.append({
                        "parent_bone": int(v_parts[0]),
                        "pos": [float(x) for x in v_parts[1:4]],
                        "norm": [float(x) for x in v_parts[4:7]],
                        "uv": [float(x) for x in v_parts[7:9]]
                    })
                self.triangles.append({
                    "texture": texture,
                    "vertices": vertices
                })

            i += 1

    def display_summary(self):
        print(f"Nodes found: {len(self.nodes)}")
        print(f"Skeleton frames: {len(self.skeleton)}")
        print(f"Triangles found: {len(self.triangles)}")

    def patch_bones(self, weapon_suffix, is_shared_bone_func):
        """Applies bone patching logic directly to the parsed data structures."""
        if not self.nodes:
            return

        bone01_id = next((n["id"] for n in self.nodes if n["name"].lower() == "bone01"), None)
        master_root_id = next((n["parent"] for n in self.nodes if n["id"] == bone01_id), None)

        need_inject_root = (master_root_id == -1 or master_root_id is None)

        if not need_inject_root:
            for n in self.nodes:
                if n["id"] == master_root_id:
                    n["name"] = "Universal_Root"
                    n["parent"] = -1
                    break

        # Add suffixes to weapon-specific bones
        for n in self.nodes:
            if not is_shared_bone_func(n["name"]):
                n["name"] += weapon_suffix

        # Inject Universal_Root if missing
        new_root_old_id = None
        if need_inject_root:
            new_root_old_id = max((n["id"] for n in self.nodes), default=-1) + 1
            self.nodes.append({"id": new_root_old_id, "name": "Universal_Root", "parent": -1})
            for n in self.nodes:
                if n["parent"] == -1 and n["id"] != new_root_old_id:
                    n["parent"] = new_root_old_id

        # Sort to ensure parents are defined before children
        children = defaultdict(list)
        root_ids = []
        for n in self.nodes:
            if n["parent"] == -1:
                root_ids.append(n["id"])
            else:
                children[n["parent"]].append(n["id"])

        sorted_nodes = []
        queue = root_ids[:]
        while queue:
            curr = queue.pop(0)
            node_dict = next(n for n in self.nodes if n["id"] == curr)
            sorted_nodes.append(node_dict)
            queue.extend(children[curr])

        # Create Old-to-New ID Map and Update Node IDs
        old_to_new = {}
        for new_id, n in enumerate(sorted_nodes):
            old_to_new[n["id"]] = new_id

        for n in sorted_nodes:
            n["id"] = old_to_new[n["id"]]
            if n["parent"] != -1:
                n["parent"] = old_to_new[n["parent"]]

        # Sort the actual nodes list by their new IDs
        self.nodes = sorted(sorted_nodes, key=lambda x: x["id"])

        # Update Skeleton Animation IDs
        for time, bones in self.skeleton.items():
            for b in bones:
                if b["bone_id"] in old_to_new:
                    b["bone_id"] = old_to_new[b["bone_id"]]

            # Inject animation frames for the new root so it doesn't crash
            if need_inject_root:
                injected_id = old_to_new[new_root_old_id]
                bones.append({
                    "bone_id": injected_id,
                    "pos": [0.0, 0.0, 0.0],
                    "rot": [0.0, 0.0, 0.0]
                })

            # Sort bones in the frame to maintain order
            bones.sort(key=lambda x: x["bone_id"])

        # Update Mesh Vertices (Triangles) IDs
        for tri in self.triangles:
            for v in tri["vertices"]:
                if v["parent_bone"] in old_to_new:
                    v["parent_bone"] = old_to_new[v["parent_bone"]]

    def __str__(self):
        """Returns the parsed data in the standard SMD file format."""
        lines = ["version 1"]

        # Nodes section
        if len(self.nodes) > 0:
            lines.append("nodes")
            for node in self.nodes:
                # Format: ID "Name" ParentID
                lines.append(f"  {node['id']} \"{node['name']}\" {node['parent']}")
            lines.append("end")

        # Skeleton Section
        lines.append("skeleton")
        for time, bones in self.skeleton.items():
            lines.append(f"  time {time}")
            for b in bones:
                # Format: ID PosX PosY PosZ RotX RotY RotZ
                pos = " ".join(f"{x:.6f}" for x in b['pos'])
                rot = " ".join(f"{x:.6f}" for x in b['rot'])
                lines.append(f"    {b['bone_id']} {pos} {rot}")
        lines.append("end")

        # Triangles section
        if len(self.triangles) > 0:
            lines.append("triangles")
            for tri in self.triangles:
                # First line: Texture name
                lines.append(tri['texture'])
                # Next 3 lines: Vertex data
                for v in tri['vertices']:
                    # Format: ParentBone PosX PosY PosZ NormX NormY NormZ U V
                    pos = " ".join(f"{x:.6f}" for x in v['pos'])
                    norm = " ".join(f"{x:.6f}" for x in v['norm'])
                    uv = " ".join(f"{x:.6f}" for x in v['uv'])
                    lines.append(f"  {v['parent_bone']} {pos} {norm} {uv}")
            lines.append("end")
        lines.append("")

        return "\n".join(lines)