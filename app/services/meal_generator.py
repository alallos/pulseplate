"""Mock meal plan generator (LLM placeholder). Later: real Grok API call."""

from app.models.biometrics import BiometricData, MealPlanResponse


def generate_meal_plan_mock(data: BiometricData) -> MealPlanResponse:
    """
    Mock version: creates a plausible meal plan based on biometrics.
    In production: replace with async call to Grok API.
    """
    # Simple logic to vary output based on input (mimics LLM reasoning)
    recovery_adj = "light and easy-to-digest" if data.recovery_status in ["low", "fair"] else "nutrient-dense and energizing"
    goal_adj = "lower-carb for fat loss" if "fat_loss" in data.goals else "balanced macros"

    summary = (
        f"Today's plan is {recovery_adj} and {goal_adj}, "
        f"targeting ~{data.calorie_target} kcal with your {data.diet_style} style."
    )

    # Sample meals (vary slightly)
    meals = [
        {
            "type": "Breakfast",
            "name": "Greek Yogurt Parfait",
            "description": "High-protein start with berries and nuts (skip if allergic). ~450 kcal",
            "calories": 450,
        },
        {
            "type": "Lunch",
            "name": "Grilled Chicken Salad",
            "description": "Lean protein + veggies for steady energy. ~550 kcal",
            "calories": 550,
        },
        {
            "type": "Dinner",
            "name": "Baked Salmon with Quinoa",
            "description": "Omega-3 rich recovery meal. ~600 kcal",
            "calories": 600,
        },
        {
            "type": "Snack 1",
            "name": "Apple with Almond Butter",
            "description": "Quick energy boost. ~200 kcal",
            "calories": 200,
        },
    ]

    # Grocery list (simple aggregation)
    grocery_list = [
        {"item": "Greek yogurt (plain, full-fat)", "quantity": "500g"},
        {"item": "Mixed berries (fresh or frozen)", "quantity": "300g"},
        {"item": "Chicken breast", "quantity": "400g"},
        {"item": "Salmon fillet", "quantity": "300g"},
        {"item": "Quinoa", "quantity": "200g dry"},
        {"item": "Apples", "quantity": "4 medium"},
        {"item": "Almond butter", "quantity": "1 jar"},
        # Add more based on allergies/diet in future
    ]

    return MealPlanResponse(
        summary=summary,
        meals=meals,
        grocery_list=grocery_list,
    )
