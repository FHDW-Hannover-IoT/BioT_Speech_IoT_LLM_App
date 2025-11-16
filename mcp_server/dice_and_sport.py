from mcp.server.fastmcp import FastMCP
import random

class SportRecommender:
    def __init__(self):
        self.sports = {
            1: "walking",
            2: "jogging",
            3: "swimming",
            4: "cycling",
            5: "fitness studio"
        }

    def recommend(self) -> dict:
        dice = random.randint(1, 5)
        print(f"[SportRecommender] Rolled a {dice}, recommending {self.sports[dice]}")
        return {"sport": self.sports[dice], "dice_roll": dice}

mcp = FastMCP("StatefulServer")
recommender = SportRecommender()

@mcp.tool()
def recommend_sport() -> dict:
    """Returns a sport recommendation (calls the dice simulator)."""
    return recommender.recommend()

if __name__ == "__main__":
    # Run server with streamable_http transport
    mcp.run(transport="streamable-http")