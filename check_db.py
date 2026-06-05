from core.database import get_opportunities

opps = get_opportunities()
print(f"Total opportunities: {len(opps)}\n")
for o in opps:
    print(f"Company: {o['company']}")
    print(f"Role: {o['role']}")
    print(f"Status: {o['status']}")
    print(f"Match Score: {o['match_score']}")
    print("---")
