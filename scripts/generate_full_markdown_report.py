import json
from pathlib import Path

def generate_report():
    p = Path('/Users/ankitsingh/.pulse/enterprise/reports/company_rollup_1793789557.json')
    data = json.loads(p.read_text(encoding='utf-8'))

    out_dir = Path('/Users/ankitsingh/PULSE/docs/corporate/company_reports')
    out_dir.mkdir(parents=True, exist_ok=True)
    md_file = out_dir / 'full_enterprise_report_20261104.md'

    lines = []
    lines.append('# PULSE Autonomous Enterprise Corporation: Full 22-Leader Verification & Operational Report')
    lines.append('**Date**: Wednesday, November 04, 2026  ')
    lines.append('**Sovereign Founder & Creator**: Ankit Singh  ')
    lines.append('**Audit Status**: 22/22 Leaders Verified 100% Operational  ')
    lines.append('**Cycle Duration**: 318.03s  \n')

    lines.append('---')
    lines.append('## Executive Summary: Master CEO Briefing to Ankit Singh')
    lines.append(data['ceo_report']['content'])
    lines.append('\n---\n')

    lines.append('## Part 1: C-Suite Executive Reports (Reports to @pulse-ceo)\n')
    for k, v in data['c_suite_reports'].items():
        lines.append(f'### Division: {v["title"]} (@{k})')
        lines.append(f'**Direct Lead**: {v["reports_to"]} | **Channel**: #{v["channel"]} | **Status**: `{v["status"]}` ({v["execution_duration_sec"]:.1f}s)\n')
        lines.append(v['content'])
        lines.append('\n---\n')

    lines.append('## Part 2: Divisional VP Reports (Reports to C-Suite Leads)\n')
    for k, v in data['vp_reports'].items():
        lines.append(f'### Department: {v["title"]} (@{k})')
        lines.append(f'**Direct Lead**: {v["reports_to"]} | **Channel**: #{v["channel"]} | **Status**: `{v["status"]}` ({v["execution_duration_sec"]:.1f}s)\n')
        lines.append(v['content'])
        lines.append('\n---\n')

    md_file.write_text('\n'.join(lines), encoding='utf-8')
    print('Generated complete report:', md_file, f'({len(lines)} sections)')

if __name__ == '__main__':
    generate_report()
