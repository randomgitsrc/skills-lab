Find the element described by the user's query in this image, and return its bounding box.

Look for the element wherever it might be. It could be a UI button, a physical object in a photo, text on a sign, a face in a crowd, a region on a map — adapt to the image type.

Output a JSON array of matches:
[{"label": "what you found", "box": [a, b, c, d], "confidence": "high|medium|low", "type": "element|effect|region"}]

Rules:
- Only return elements you can actually see and locate.
- If the element has no clear boundary (like a glow or shadow), mark confidence ≤ medium and type as "effect" or "region".
- If the element is partially hidden or at an angle, still return it with reduced confidence.
- If nothing matches, return [] — never fabricate.
- Use whatever coordinate format you're trained on. The caller handles conversion.
- No markdown wrapping. Just the JSON array.
