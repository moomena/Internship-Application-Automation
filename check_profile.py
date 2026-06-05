from core.database import get_profile, get_opportunities

profile = get_profile()
print(f"Profile: {profile}\n")

opps = get_opportunities(status="pending")
print(f"Pending opportunities: {len(opps)}\n")
for o in opps[:3]:
    print(f"  - {o['company']} - {o['role']}")
