const assert=require('node:assert/strict');
const {visibleSpan,nesting,traceLayout,modelBudget}=require('./vendor/robot-shop/web/static/trace-view.js');
const root={span_id:'root',parent_span_id:null,service_name:'any-application',kind:'SERVER',attributes:{'http.request.method':'POST'}};
const wrapper={span_id:'wrapper',parent_span_id:'root',kind:'INTERNAL',attributes:{}};
const tool={span_id:'tool',parent_span_id:'wrapper',kind:'INTERNAL',attributes:{'gen_ai.operation.name':'execute_tool'}};
assert(visibleSpan(root));assert(visibleSpan(tool));assert(!visibleSpan(wrapper));
assert(visibleSpan({kind:'CLIENT',attributes:{'db.system.name':'redis'}}));
const all=new Map([root,wrapper,tool].map(s=>[s.span_id,s])),shown=new Set(['root','tool']);
assert.equal(nesting(tool,all,shown),1); // Real ancestor retained across hidden middleware.
assert.equal(nesting(tool,new Map(),shown),0); // Missing spans do not invent an edge.
const cycle=new Map(all);cycle.set('wrapper',{...wrapper,parent_span_id:'wrapper'});
assert.equal(nesting(tool,cycle,shown),0); // Cyclic metadata cannot hang the renderer.
console.log('Native trace filtering and ancestry checks passed');
const late={...tool,span_id:'late',start_ns:1200,end_ns:1210,parent_span_id:'root'};
const layout=traceLayout([{...root,start_ns:100,end_ns:500},late]);
assert.equal(layout.extent,1110); // A late committed write extends beyond its failed caller.
assert.equal(nesting(late,layout.all,layout.visible),1);
assert.deepEqual(traceLayout([]).shown,[]);
assert.equal(modelBudget({turns_used:11,daily_turn_limit:20,model:'gemini'}).remaining,9);
assert.equal(modelBudget({turns_used:21,daily_turn_limit:20,model:'gemini'}).remaining,0);
assert.equal(modelBudget({turns_used:-1,daily_turn_limit:20}),null);
console.log('Late side-effect timing and live budget checks passed');
