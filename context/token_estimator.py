class TokenEstimator:
    CHARS_PER_TOKEN = 4

    def estimate_text(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // self.CHARS_PER_TOKEN)

    def estimate_message(self, messages) -> int:
        total = 0
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "") or ""
            else:
                content = getattr(msg, "content", "") or ""
            if isinstance(content, str):
                total += self.estimate_text(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += self.estimate_text(str(block.get("text", "")))
                    else:
                        total += self.estimate_text(str(getattr(block, "text", "") or ""))
        return total