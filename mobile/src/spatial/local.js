// Instant, offline understanding of the GENERIC view commands ("rotate it 90 degrees", "zoom in", "explode it", "top view",
// "make it transparent", "show me the inside"...). These work for any object. Anything that names a thing or a part goes to
// AURA's AI planner instead (spatial.command on the PC), which knows the current scene.
const FILLER = /^(please |can you |could you |aura,? |now |ok(ay)? |and |then |hey |just )+|( please| for me| now| a bit| a little| slightly)+$/g;
const NUM = { a: 1, one: 1, two: 2, three: 3, half: 0.5, quarter: 0.25, ninety: 90, 'forty five': 45, 'one hundred eighty': 180, 'a hundred and eighty': 180 };

function degrees(s, dflt) {
  const m = s.match(/(-?\d+(?:\.\d+)?)\s*(degrees?|deg|°)?/);
  if (m) return parseFloat(m[1]);
  for (const [w, n] of Object.entries(NUM)) if (n >= 45 && s.includes(w)) return n;
  if (/half (a )?turn|around|180/.test(s)) return 180;
  if (/quarter/.test(s)) return 90;
  return dflt;
}

/** Returns a list of ops, or null if this needs the AI (or isn't a view command). hasSel: something is selected. */
export function parseLocal(text, { hasSel = false, loaded = true } = {}) {
  const s = String(text || '').toLowerCase().replace(/[.!?]+$/g, '').replace(/\s+/g, ' ').trim().replace(FILLER, '').trim();
  if (!s || !loaded) return null;
  const it = '(it|this|that|the (model|object|thing))?';
  const R = (re) => new RegExp(`^${re}$`).test(s);

  if (R('(undo|undo (that|it)|go back|reverse (that|it)|take (that|it) back|cancel (that|it))')) return [{ op: 'undo' }];
  if (R('(reset|reset (the )?view|reset everything|restore( everything| all)?|start over|recenter|re-center|center it|centre it|back to (the )?start|put everything back( to normal)?)')) return [{ op: 'restore' }];
  if (R('(unhide( it| everything| all)?|bring (it|them|everything|the hidden (parts|pieces)) back|show (the )?hidden( parts| pieces)?)')) return [{ op: 'show', targets: [] }];
  if (R('(unisolate|exit isolat(e|ion)|stop isolating|un-isolate|show the rest|bring (back )?the rest( back)?)')) return [{ op: 'unisolate' }];
  if (R('((turn on |switch to |go )?holo(gram)?( mode| view| on)?|make it (a )?hologram)')) return [{ op: 'hologram', on: true }];
  if (R('((turn off |exit |stop )holo(gram)?( mode)?|holo(gram)? off|normal (view|mode|look)|make it real( again)?)')) return [{ op: 'hologram', on: false }];
  if (R(`(take ${it} ?apart|dismantle ${it}?|disassemble ${it}?) ?(step by step|piece by piece|one (piece|part) at a time|slowly)`)) return [{ op: 'disassemble', step: true }];
  if (R(`(put ${it} ?back together|reassemble ${it}?|assemble ${it}?|rebuild ${it}?) ?(step by step|piece by piece|one (piece|part) at a time|slowly)`)) return [{ op: 'reassemble', step: true }];
  if (R('((identify|name|label|scan) (the |all the |these |all )?(pieces|parts|components)|what are (these|the|all these) (pieces|parts|components)|ai scan|scan it)')) return [{ op: 'identify' }];
  if (R(`(dissect ${it}?|cut ${it} ?into (its )?(real )?parts|split ${it} ?into (its )?parts|separate (all )?(the|its) parts|break ${it} ?down( into parts)?|show (me )?(what'?s|what is) inside( ${it})?)`)) return [{ op: 'dissect' }];
  if (hasSel && R('(inspect (it|this|that)|examine (it|this|that)|look closer( at (it|this|that))?)')) return [{ op: 'inspect', target: 'it' }];
  if (hasSel && R('(pull (it|this|that) out|take (it|this|that) out|move (it|this|that) out|separate (it|this|that))')) return [{ op: 'move', targets: ['it'], direction: 'out', amount: 0.5 }];
  if (hasSel && R('(put (it|this|that) back|return (it|this|that)|move (it|this|that) back)')) return [{ op: 'reassemble', targets: ['it'] }];
  if (R(`(turn|flip|spin|rotate) ${it} ?(around|round)`)) return [{ op: 'rotate', axis: 'y', degrees: 180 }];
  if (R(`(flip|turn) ${it} ?upside down`)) return [{ op: 'rotate', axis: 'x', degrees: 180 }];
  let m = s.match(/^(rotate|turn|tilt)( it| this| that| the model| the object)?( to the| towards the)?( (left|right|up|down|clockwise|anti ?clockwise|counter ?clockwise))?( by)?( (.+))?$/);
  if (m) {
    const rest = (m[8] || '').trim();
    if (rest && !/^(-?\d+(\.\d+)?|a|one|two|ninety|forty five|a quarter|quarter|half)( (degrees?|deg|°|turns?|way))?( (a )?turn)?$/.test(rest)) return null;
    const dir = m[5] || 'right';
    const deg = degrees(rest, dir === 'up' || dir === 'down' ? 45 : 90);
    if (dir === 'up' || dir === 'down') return [{ op: 'rotate', axis: 'x', degrees: dir === 'up' ? -deg : deg }];
    if (/anti|counter/.test(dir)) return [{ op: 'rotate', axis: 'z', degrees: deg }];
    if (/clockwise/.test(dir)) return [{ op: 'rotate', axis: 'z', degrees: -deg }];
    return [{ op: 'rotate', axis: 'y', degrees: dir === 'left' ? -deg : deg }];
  }
  if (R(`(spin|turntable|keep (it )?(spinning|rotating)|rotate continuously|auto ?rotate) ?${it}?`)) return [{ op: 'spin', on: true }];
  if (R('(stop|stop (spinning|rotating|the spin|moving)|hold (it )?still|freeze)')) return [{ op: 'spin', on: false }, { op: 'animate', name: null, on: false }];
  if (R('(zoom in|closer|get closer|go closer|bring (it )?closer|enlarge|make (it )?bigger|bigger)( more)?')) return [{ op: 'zoom', factor: 1.5 }];
  if (R('(zoom out|further|farther|go back|move back|make (it )?smaller|smaller)( more)?')) return [{ op: 'zoom', factor: 0.66 }];
  if (R(`(explode|exploded view|blow (it )?apart|take (it )?apart|separate (the )?(parts|pieces)|pull (it )?apart) ?${it}?`)) return [{ op: 'explode', amount: 0.65 }];
  if (R('explode (it )?(more|further)')) return [{ op: 'explode', amount: 1 }];
  if (R(`(put ${it} ?back( together)?|reassemble( ${it})?|assemble( ${it})?|collapse( ${it})?|unexplode|put the (parts|pieces) back( together)?|bring (the )?(pieces|parts) back together|get back together)`)) return [{ op: 'reassemble' }];
  if (R(`(make ${it} ?(transparent|see.?through|translucent|ghost(ed|ly)?)|x.?ray( view| mode| on)?|(go|switch to) x.?ray)`)) return hasSel && /^make (it|this|that)/.test(s) ? [{ op: 'opacity', targets: ['it'], value: 0.2 }] : [{ op: 'xray', on: true }];
  if (R(`(make ${it} ?(solid|opaque|normal)|x.?ray off|turn off x.?ray|no x.?ray|solid( view)?)`)) return [{ op: 'xray', on: false }, { op: 'opacity', targets: [], value: 1 }];
  if (R('(wire ?frame( view| mode| on)?|show (the )?(wire ?frame|mesh))')) return [{ op: 'wireframe', on: true }];
  if (R('(wire ?frame off|no wire ?frame|hide (the )?(wire ?frame|mesh))')) return [{ op: 'wireframe', on: false }];
  m = s.match(/^((show me |go to |switch to )?(the )?(front|back|rear|top|bottom|left|right|side|isometric|iso|3\/4)( view| side)?|(view( it)?|look at it|see it|show( me)? it) from (the )?(front|back|rear|top|bottom|above|below|left|right|side))$/);
  if (m) {
    const w = (m[4] || m[8] || '').replace('rear', 'back').replace('side', 'right').replace('above', 'top').replace('below', 'bottom').replace('isometric', 'iso').replace('3/4', 'iso');
    return [{ op: 'view', preset: w }];
  }
  if (R(`(show me |let me see |i want to see )?(the )?(inside|interior)( of ${it})?|cut ${it} ?(open|in half)|(cross.?section|section|cutaway|cut.?away)( view)?( ${it})?|slice ${it}`)) return [{ op: 'section', axis: 'x', value: 0 }];
  if (R('(close (the )?(section|cut)|remove (the )?(section|cut)|uncut|no (section|cut))')) return [{ op: 'section', axis: null, value: null }];
  if (hasSel && R('(hide (it|this|that)|get rid of (it|this|that)|remove (it|this|that))')) return [{ op: 'hide', targets: ['it'] }];
  if (hasSel && R('(isolate (it|this|that)|show (only|just) (it|this|that)|only (it|this|that))')) return [{ op: 'isolate', targets: ['it'] }];
  if (hasSel && R('(focus( on)? (it|this|that)|zoom (in )?(on|to) (it|this|that))')) return [{ op: 'focus', target: 'it' }];
  if (hasSel && R('(deselect|unselect|clear (the )?selection)')) return [{ op: 'select', targets: [] }];
  if (R('(show (me )?(everything|all|all (the )?parts|it all)|unhide( all| everything)?|bring (everything|it all) back)')) return [{ op: 'showall' }];
  if (R('((show|turn on|add) (the )?labels|label (it|everything|the parts)|labels on|name the parts)')) return [{ op: 'label', on: true }];
  if (R('((hide|turn off|remove) (the )?labels|labels off|no labels)')) return [{ op: 'label', on: false }];
  if (R('(full ?screen|go full ?screen|make it full ?screen|exit full ?screen)')) return [{ op: 'fullscreen' }];
  if (R(`(animate ${it}|play( the)? animation|show (me )?how it (works|moves)|make it move|start (the )?(animation|motion)|(animate|play) (it|this))`)) return [{ op: 'animate', name: 'auto', on: true }];
  if (R('(stop|pause) (the )?(animation|motion|moving)')) return [{ op: 'animate', name: null, on: false }];
  return null;
}
