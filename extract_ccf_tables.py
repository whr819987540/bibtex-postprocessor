import re
import csv
import os
from html.parser import HTMLParser

class TableExtractor(HTMLParser):
    """Extract rows from HTML tables"""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.current_row = []
        self.tables = []
        self.current_table = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.in_table = True
            self.current_table = []
        elif tag == 'tr':
            self.in_tr = True
            self.current_row = []
        elif tag in ('td', 'th'):
            self.in_td = True

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_table = False
            if self.current_table:
                self.tables.append(self.current_table)
            self.current_table = []
        elif tag == 'tr':
            self.in_tr = False
            if self.current_row:
                self.current_table.append(self.current_row)
            self.current_row = []
        elif tag in ('td', 'th'):
            self.in_td = False

    def handle_data(self, data):
        if self.in_td:
            self.current_row.append(data.strip())


def parse_ccf_markdown(filepath):
    """Parse CCF markdown file and extract all tables with context"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')

    # Track current context
    current_section = None  # 期刊 or 会议
    current_category = None  # 大类 like 计算机体系结构/并行与分布计算/存储系统
    current_level = None  # A, B, C

    # Patterns
    section_pattern = re.compile(r'^# 中国计算机学会推荐国际学术(期刊|会议)$')
    category_pattern = re.compile(r'^#?\s*\((.*?)\)\s*$')  # 大类 in parentheses
    level_pattern = re.compile(r'^#?\s*([一二三])[、,]\s*([ABC])类\s*$')

    level_map = {'一': 'A', '二': 'B', '三': 'C'}

    all_records = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check for section header
        m = section_pattern.match(line)
        if m:
            current_section = m.group(1)
            i += 1
            continue

        # Check for category
        m = category_pattern.match(line)
        if m:
            cat = m.group(1)
            if cat and len(cat) > 2:  # Not just empty or short
                current_category = cat
            i += 1
            continue

        # Check for level
        m = level_pattern.match(line)
        if m:
            current_level = level_map.get(m.group(1), m.group(2))
            i += 1
            continue

        # Check for table
        if line.startswith('<table>'):
            # Extract full table
            table_html = ''
            j = i
            while j < len(lines):
                table_html += lines[j] + '\n'
                if '</table>' in lines[j]:
                    break
                j += 1
            i = j + 1

            # Parse table
            parser = TableExtractor()
            parser.feed(table_html)
            for table in parser.tables:
                for row in table:
                    if len(row) >= 5:
                        # Skip header row
                        if row[0] == '序号':
                            continue
                        record = {
                            '类型': current_section or '',
                            '大类': current_category or '',
                            '等级': current_level or '',
                            '期刊简称': row[1],
                            '期刊全称': row[2],
                            '出版社': row[3],
                            '网址': row[4],
                        }
                        all_records.append(record)
            continue

        i += 1

    return all_records


def main():
    md_path = r'./中国计算机学会推荐国际学术会议和期刊目录-2026.md'
    output_path = r'./ccf_2026_journals_conferences.csv'

    records = parse_ccf_markdown(md_path)

    print(f"总共提取到 {len(records)} 条记录")

    # Show summary by category and level
    from collections import Counter
    summary = Counter()
    for r in records:
        key = (r['类型'], r['大类'], r['等级'])
        summary[key] += 1

    print("\n各类别统计:")
    for (typ, cat, level), count in sorted(summary.items()):
        print(f"  {typ} | {cat} | {level}类: {count}条")

    # Write CSV
    fieldnames = ['类型', '大类', '等级', '期刊简称', '期刊全称', '出版社', '网址']
    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"\nCSV已保存到: {output_path}")


if __name__ == '__main__':
    main()
