# Scaled dot-product attention
> Why transformers divide by the square root of d_k

Attention lets each token pull information from every other token. We'll build the formula piece by piece, then run a tiny example by hand.
## Three roles per token

- **Query**: what this token is looking for {#query}
- **Key**: what this token offers to be matched on {#key}
- **Value**: the content it hands over when matched {#value}

Every token is projected into three vectors. The [query](#query) says what a token is looking for. The [key](#key) is what other tokens match against. And the [value](#value) is what actually gets passed along once a match is found.

## The formula

```math
\text{Attention}(Q, K, V) = \text{softmax}\!\left(\frac{QK^\top}{\sqrt{d_k}}\right) V
```

Read it inside out. [Q K transpose](# "QK^\top") takes the dot product of every query with every key, giving a grid of raw similarity scores.

Then we [divide by the square root of d k](# "\sqrt{d_k}"), the key dimension. Hold that thought; it's the main event later.

[Softmax](# "softmax") turns each row of scores into weights that are positive and sum to one. Finally, multiplying by [V](# "V") blends the value vectors using those weights.
## Tiny example: one query, three keys

| Vector | Components | Value |
|---|---|---|
| q | 1, 0, 1, 0 | – |
| k1 | 1, 0, 1, 0 | v1 = 1, 0 |
| k2 | 0, 1, 0, 1 | v2 = 0, 1 |
| k3 | 1, 1, 0, 0 | v3 = 1, 1 |

Here's a toy setup with key dimension four. We have [one query](#row-1), and three tokens, each with a key and a two dimensional value.

[Key one](#row-2) points the same way as the query. [Key two](#row-3) is orthogonal to it. [Key three](#row-4) overlaps partly.

## Step 1: scores, then scale

```math
\begin{aligned}
q \cdot k_1 &= 2, \quad q \cdot k_2 = 0, \quad q \cdot k_3 = 1 \\
\sqrt{d_k} &= \sqrt{4} = 2 \\
\text{scaled} &= [\,1,\; 0,\; 0.5\,]
\end{aligned}
```

First, the [dot products](# "q \cdot k_1"). Key one matches on two positions, so it scores two. Key two scores zero, and key three scores one.

The key dimension is four, so we [divide by two](# "\sqrt{4} = 2"). That gives [scaled scores](# "\text{scaled}") of one, zero, and one half.
## Step 2: softmax and blend

```math
\begin{aligned}
w &= \frac{[\,e^{1},\; e^{0},\; e^{0.5}\,]}{e^{1}+e^{0}+e^{0.5}} = \frac{[\,2.718,\; 1,\; 1.649\,]}{5.367} \approx [\,0.51,\; 0.19,\; 0.31\,] \\
\text{out} &= 0.51\,v_1 + 0.19\,v_2 + 0.31\,v_3 \approx [\,0.81,\; 0.49\,]
\end{aligned}
```

Softmax [exponentiates](# "e^{1}") each score and divides by the total. The weights come out at roughly [half, a fifth, and a third](# "0.51,\; 0.19,\; 0.31").

The [output](# "\text{out}") is the weighted blend of the values: about point eight one and point four nine. Key one dominates, but the others still contribute.

## Why divide by the square root of d k?

```math
\begin{aligned}
q_i, k_i &\sim \text{mean } 0,\ \text{variance } 1 \\
\operatorname{Var}(q \cdot k) &= \sum_{i=1}^{d_k} \operatorname{Var}(q_i k_i) = d_k \\
\operatorname{Var}\!\left(\frac{q \cdot k}{\sqrt{d_k}}\right) &= 1
\end{aligned}
```

Assume the [query and key components](# "q_i, k_i") are independent, with mean zero and variance one, which is roughly what initialisation gives you.

The dot product is a sum of d k such products, each with variance one. So the [variance of the score is d k](# "= d_k"), and its spread grows like the square root of d k.

Dividing by the square root of d k brings the [variance back to one](# "= 1"), no matter how wide the model is.
## Without scaling, softmax saturates

| Case | Scores | Weights |
|---|---|---|
| Our example, scaled | 1, 0, 0.5 | 0.51, 0.19, 0.31 |
| Our example, unscaled | 2, 0, 1 | 0.67, 0.09, 0.24 |
| d_k = 512, unscaled | 45, 0, 22 | 1.00, 0.00, 0.00 |

Even in our tiny example, [skipping the scale](#row-2) sharpens the weights noticeably.

Now picture a real model with [d k of five hundred twelve](#row-3). Scores have a spread of about twenty-three, so gaps of twenty or more are normal. Softmax becomes effectively one-hot.

That's the real problem. The softmax gradient is proportional to w times one minus w, which is near zero when every weight is zero or one. Training stalls. Scaling keeps scores in the range where softmax still has a useful slope.

## Recap

- **Scores**: dot each query with every key {#scores}
- **Scale**: divide by root d_k so score variance stays at one {#scale}
- **Softmax**: turn scores into weights that sum to one {#softmax}
- **Blend**: weighted sum of values is the output {#blend}

To recap: compute [scores](#scores), [scale](#scale) them by the square root of the key dimension, [softmax](#softmax) them into weights, and [blend](#blend) the values. The scale is cheap, and it's what keeps attention trainable as models get wider.
