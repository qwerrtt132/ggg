"""Role templates: starting points for a team member. The team builder adds one as a new member."""

TEMPLATES = [
    {
        "id": "researcher",
        "name": "Researcher",
        "role": "Find and check the facts the task needs. Say where each fact came from and how sure you are.",
        "tools": ["fetch"],
    },
    {
        "id": "writer",
        "name": "Writer",
        "role": "Draft the content the task asks for, clearly and concisely. Keep drafts in the workspace.",
        "tools": ["files"],
    },
    {
        "id": "critic",
        "name": "Critic",
        "role": "Review drafts for mistakes, gaps, and weak wording. Send the writer concrete fixes, not general praise.",
        "tools": [],
    },
    {
        "id": "verifier",
        "name": "Verifier",
        "role": "Check the lead's final answer against the user's goal before it is delivered.",
        "tools": [],
    },
    {
        "id": "coder",
        "name": "Coder",
        "role": "Write the code the task needs as files in the workspace, and explain what each file does.",
        "tools": ["files"],
    },
]


def template_by_id(template_id):
    return next((template for template in TEMPLATES if template["id"] == template_id), None)
