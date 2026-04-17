# ML L3 — Transformers and Self-Attention

Transformers are a sequence-to-sequence architecture introduced in the 2017 paper
"Attention Is All You Need". Unlike RNNs, they process all tokens in parallel using
self-attention, which lets every token directly attend to every other token in the
input.

## Self-attention

Each input token is projected into three vectors: a query Q, a key K, and a value V.
The attention score from token i to token j is the dot product of Q_i and K_j,
scaled by the square root of the key dimension, then softmaxed across j. The output
for token i is the weighted sum of V_j across all j, using the softmaxed attention
scores as weights.

The scaling factor 1/sqrt(d_k) prevents the dot products from growing too large,
which would push the softmax into regions with vanishing gradients.

## Multi-head attention

A single attention head learns one relationship pattern. Multi-head attention runs
several attention computations in parallel, each with independently learned Q/K/V
projections, and concatenates their outputs. This lets the model capture different
types of relationships (syntactic, semantic, positional) simultaneously.

The original paper used 8 heads. Larger models use more.

## Positional encoding

Because self-attention is permutation-invariant, the model has no inherent sense of
token order. The original transformer adds sinusoidal positional encodings to the
input embeddings. Modern variants often use learned positional embeddings or
rotary positional embeddings (RoPE), which encode position as a rotation in the
query and key space.

## Why transformers dominate

Three advantages over RNNs:
1. Parallel computation over sequence length (no sequential dependency).
2. Constant-depth path between any two tokens (vs O(n) for RNNs).
3. Better gradient flow, enabling much deeper networks.

The tradeoff is O(n^2) complexity in sequence length, which motivated later work
on efficient attention variants like Performer and Longformer.
