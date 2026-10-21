import json
from starter.agent import Agent


# --------------------------------------------------
# Load product titles from the catalog
# --------------------------------------------------
def load_catalog_titles(path="data/catalog.jsonl"):
    titles = {}

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            product = json.loads(line)

            asin = str(product.get("parent_asin", "")).strip()
            title = str(product.get("title", "Unknown product")).strip()

            if asin:
                titles[asin] = title

    return titles


# --------------------------------------------------
# Create agent
# --------------------------------------------------
print("Loading catalog...")

catalog_titles = load_catalog_titles()

agent = Agent("data/catalog.jsonl")

session_id = "demo_session"

user_profile = {
    "preference_tags": ["comfort", "style", "durability"],
    "purchase_frequency": "medium",
    "rating_style": "balanced",
    "summary": "Prefers comfortable and practical products."
}

agent.reset(session_id, user_profile)


# --------------------------------------------------
# Start demo
# --------------------------------------------------
print("\n" + "=" * 70)
print("SHOPPING COPILOT - CONVERSATIONAL SEARCH DEMO")
print("=" * 70)

user_message = "I'm looking for running shoes, but I'm still exploring."


for turn in range(1, 6):

    print(f"\nTURN {turn}")
    print("-" * 70)

    print("\nUSER:")
    print(user_message)

    response = agent.respond(
        session_id=session_id,
        user_message=user_message,
        turn=turn,
        top_k=10
    )

    print("\nSHOPPING COPILOT:")
    print(response.get("message", ""))

    recommendations = response.get("recommendations", [])

    print("\nTOP RECOMMENDATIONS:")

    if recommendations:

        for i, item in enumerate(recommendations, start=1):

            if isinstance(item, dict):
                asin = str(item.get("parent_asin", ""))
            else:
                asin = str(item)

            title = catalog_titles.get(asin, "Unknown product")

            # Prevent extremely long titles from making the demo messy
            if len(title) > 85:
                title = title[:82] + "..."

            print(f"{i}. {title}")
            print(f"   ASIN: {asin}")

    else:
        print("No recommendations returned.")

    if turn == 5:
        break

    print("\n" + "=" * 70)

    user_message = input("\nYOUR REPLY: ").strip()

    if not user_message:
        print("\nDemo ended.")
        break


print("\n" + "=" * 70)
print("DEMO COMPLETE")
print("=" * 70)