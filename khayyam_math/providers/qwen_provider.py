"""Local Qwen back-end.

Loads the LoRA adapter from Hugging Face on first construction and
runs inference locally.  The adapter id defaults to the v4 release at
``khayyam-math/khayyam-math-qwen2.5-7b-v4``; the base model
(``Qwen/Qwen2.5-7B-Instruct``) is read from the adapter's
``adapter_config.json`` so callers don't have to specify it.

Heavy dependencies (``torch``, ``transformers``, ``peft``,
``accelerate``) live in the ``[qwen]`` extra::

    pip install khayyam-math[qwen]
"""
from __future__ import annotations

from typing import Optional


class QwenProvider:
    def __init__(
        self,
        model: str = "khayyam-math/khayyam-math-qwen2.5-7b-v4",
        device: str = "auto",
        dtype: str = "bfloat16",
        hf_token: Optional[str] = None,
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel, PeftConfig
        except ImportError as exc:
            raise ImportError(
                "The Qwen provider needs torch / transformers / peft.  "
                "Install with: pip install khayyam-math[qwen]"
            ) from exc

        self._torch = torch
        self.model_id = model

        # Resolve the base model from the adapter config so users only
        # have to remember one repo id.
        peft_config = PeftConfig.from_pretrained(model, token=hf_token)
        base_id = peft_config.base_model_name_or_path

        self.tokenizer = AutoTokenizer.from_pretrained(model, token=hf_token)
        torch_dtype = getattr(torch, dtype)
        base_model = AutoModelForCausalLM.from_pretrained(
            base_id,
            torch_dtype=torch_dtype,
            device_map=device,
            token=hf_token,
        )
        self.model = PeftModel.from_pretrained(
            base_model, model, token=hf_token,
        )
        self.model.eval()

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> str:
        torch = self._torch
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]
        inputs = self.tokenizer.apply_chat_template(
            messages,
            return_tensors="pt",
            add_generation_prompt=True,
        ).to(self.model.device)

        do_sample = temperature > 0
        with torch.no_grad():
            out = self.model.generate(
                inputs,
                max_new_tokens=max_tokens,
                do_sample=do_sample,
                temperature=temperature if do_sample else 1.0,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        gen_tokens = out[0, inputs.shape[1]:]
        return self.tokenizer.decode(gen_tokens, skip_special_tokens=True).strip()
