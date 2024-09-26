import re
import sys
import pandas as pd
import plotly.express as px
import argparse

def parse_component(lines):
    component = {}
    for line in lines:
        line = line.strip()
        if len(line) and "Number" not in line and "top" not in line:
            key, value = line.strip().split()
            component[key] = int(value)
        else:
            continue
    return component

def parse_hierarchy(hierarchy_lines):
    hierarchy = []
    for line in hierarchy_lines[2:]:
        depth = len(line) - len(line.lstrip())
        depth = max(0, int(((depth - 5) / 2) + 1))
        if not len(line.strip()):
            break
        name = line.strip().split()[0]
        hierarchy.append((name, depth))
    return hierarchy

def build_hierarchy_dict(hierarchy):
    hierarchy_dict = {}
    stack = []
    for name, depth in hierarchy:
        while len(stack) > depth:
            stack.pop()
        if stack:
            parent = stack[-1]
            hierarchy_dict[name] = parent
        else:
            hierarchy_dict[name] = None
        stack.append(name)
    return hierarchy_dict

def parse_file(filename):
    with open(filename, 'r') as f:
        content = f.read()

    blocks = re.split(r'=== (.+) ===', content)[1:]
    name_lines = list(zip(blocks[::2], blocks[1::2]))
    components = {}
    for name, lines in name_lines:
        #if "===" in name or "===" in lines:
        #    continue
        name = name.strip()
        lines = lines.split("\n")
        #print("***")
        #print(name, lines)
        if "design hierarchy" in name:
            #print(lines)
            hierarchy = parse_hierarchy(lines)
            hierarchy_dict = build_hierarchy_dict(hierarchy)
            #print(hierarchy_dict)
        else:
            components[name] = parse_component(lines)
            #print(name, components[name])

    return components, hierarchy_dict

def create_dataframe(components, hierarchy_dict):
    df = pd.DataFrame(components).T.reset_index()
    df = df.rename(columns={'index': 'name'})
    df['parent'] = df['name'].map(hierarchy_dict)
    return df

def calculate_total_resources(df):
    resource_columns = df.columns[2:]  # Exclude 'name' and 'parent' columns
    
    def sum_resources(name):
        total = df[df['name'] == name][resource_columns].iloc[0]
        children = df[df['parent'] == name]
        for _, child in children.iterrows():
            total += sum_resources(child['name'])
        return total

    for name in df['name']:
        df.loc[df['name'] == name, resource_columns] = sum_resources(name)

    return df

def plot_sunburst(df, resource_type):
    fig = px.sunburst(
        df,
        names='name',
        parents='parent',
        values=resource_type,
        title=f'Hierarchical Sunburst Chart of {resource_type} Usage'
    )
    fig.show()

def main():
    parser = argparse.ArgumentParser(description='Parse synthesis report and create sunburst plot')
    parser.add_argument('filename', help='Path to the synthesis report file')
    parser.add_argument('resource_type', help='Resource type to plot (e.g., LUT4, TRELLIS_FF)')
    args = parser.parse_args()

    components, hierarchy_dict = parse_file(args.filename)
    df = create_dataframe(components, hierarchy_dict)
    df = df.fillna(0)
    print(df.columns)
    #print(df['LUT4'])
    #df = calculate_total_resources(df)
    #print(df)
    plot_sunburst(df, args.resource_type)

if __name__ == "__main__":
    main()
