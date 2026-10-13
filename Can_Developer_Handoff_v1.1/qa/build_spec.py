"""Build the reading copy; docs/*.md remain editable sources."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=['PRODUCT.md','UX.md','BUDGET_RULES.md','ARCHITECTURE_API.md','SYNC_PROTOCOL.md','SECURITY.md','OPERATIONS.md','DECISIONS.md','ACCEPTANCE.md','CHANGELOG.md','SOURCES.md']
header='# Can / «Можно» — полная спецификация v1.1\n\n12 октября 2026. Учитывает ревью и подтверждение текущего остатка банка.\n\nПродуктовая база зафиксирована; технические документы — задание для реализации, не готовый продукт.\nОткрытые практические вопросы/политика удаления — раздел 08. Статус проверок — qa/VALIDATION.md.\nНачало работы исполнителя — DEVELOPER_HANDOFF.md.\n'
parts=[header]+[(ROOT/'docs'/f).read_text() for f in FILES]
(ROOT/'CAN_SPEC_v1.1.md').write_text('\n\n---\n\n'.join(parts),encoding='utf-8')
print('Built CAN_SPEC_v1.1.md')
