// Direction labels come from normalized trained vectors, independent of training speed.
export const labels = {up:'위쪽', down:'아래쪽', left:'왼쪽', right:'오른쪽'};
export class DirectionInput {
  constructor(model) {
    if (model?.format !== 'nu40-imu-direction-model' || model.schema_version !== 1 || model.units !== 'degrees_per_second') throw Error('지원하지 않는 방향 학습 파일');
    this.threshold = model.rate_threshold_dps;
    this.similarity = model.similarity_threshold;
    if (!Number.isFinite(this.threshold) || this.threshold <= 0 || !Number.isFinite(this.similarity) || this.similarity < 0 || this.similarity > 1) throw Error('잘못된 방향 기준값');
    this.vectors = Object.keys(labels).map(key => {
      const v = model.directions?.[key];
      if (!Array.isArray(v) || v.length !== 3 || !v.every(Number.isFinite) || !Math.hypot(...v)) throw Error('상하좌우 학습 데이터가 필요합니다');
      const magnitude = Math.hypot(...v);
      return [key, v.map(x => x / magnitude)];
    });
    this.reset();
  }
  reset() { this.previous = null; this.session = null; this.source = null; }
  classify(v) {
    if (!v.every(Number.isFinite)) return null;
    const rate = Math.hypot(...v);
    if (rate < this.threshold) return null;
    const scores = this.vectors.map(([key, n]) => ({key, score:n.reduce((sum,x,i)=>sum+x*v[i]/rate,0)})).sort((a,b)=>b.score-a.score);
    // Reject diagonal/tied matches rather than arbitrarily choosing the first label.
    if (scores[0].score < this.similarity || scores[0].score-scores[1].score < 0.08) return null;
    return {...scores[0], rate};
  }
  consume(state, gain=1) {
    if (!state?.sample || !state.source || !Number.isFinite(state.age_ms) || state.age_ms > 350) { this.reset(); return null; }
    const p = state.sample;
    if (!Number.isInteger(p.t_ms) || p.t_ms<0 || p.t_ms>0xffffffff) { this.reset(); return null; }
    const match = this.classify([p.gx,p.gy,p.gz]);
    const previous = this.previous;
    const changed = this.source !== state.source || this.session !== state.session;
    this.source = state.source; this.session = state.session; this.previous = p.t_ms;
    if (changed || previous === null) return null;
    const dtMs = (p.t_ms-previous+0x100000000)%0x100000000;
    // No integration of stale, repeated, restarted or delayed samples.
    if (dtMs === 0 || dtMs > 200 || !match || !Number.isFinite(gain) || gain<=0) return null;
    const angle = Math.min(match.rate,720)*dtMs/1000*Math.PI/180*Math.min(gain,3);
    return {...match, yaw:match.key==='left'?angle:match.key==='right'?-angle:0,
      pitch:match.key==='up'?angle:match.key==='down'?-angle:0};
  }
}
