// Rectangular minimum-cost assignment. Each row can use a dummy column to
// stay unmatched, so a gated pair never displaces a valid association.
pub(crate) fn solve(costs: &[Vec<f64>], real_cols: usize, unmatched: f64) -> Vec<(usize, usize)> {
    let rows = costs.len();
    let cols = real_cols + rows;
    let mut row_price = vec![0.; rows + 1];
    let mut col_price = vec![0.; cols + 1];
    let mut owner = vec![0; cols + 1];
    let mut parent = vec![0; cols + 1];
    for row in 1..=rows {
        owner[0] = row;
        let mut j = 0;
        let mut used = vec![false; cols + 1];
        let mut slack = vec![f64::INFINITY; cols + 1];
        loop {
            used[j] = true;
            let r = owner[j];
            let mut best = f64::INFINITY;
            let mut next = 0;
            for k in 1..=cols {
                if used[k] {
                    continue;
                }
                let cost = costs[r - 1].get(k - 1).copied().unwrap_or(unmatched);
                let reduced = cost - row_price[r] - col_price[k];
                if reduced < slack[k] {
                    slack[k] = reduced;
                    parent[k] = j
                }
                if slack[k] < best {
                    best = slack[k];
                    next = k
                }
            }
            for k in 0..=cols {
                if used[k] {
                    row_price[owner[k]] += best;
                    col_price[k] -= best
                } else {
                    slack[k] -= best
                }
            }
            j = next;
            if owner[j] == 0 {
                break;
            }
        }
        while j != 0 {
            let previous = parent[j];
            owner[j] = owner[previous];
            j = previous;
        }
    }
    (1..=real_cols)
        .filter(|&c| owner[c] != 0)
        .map(|c| (owner[c] - 1, c - 1))
        .collect()
}
