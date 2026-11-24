# -*- coding: utf-8 -*-
"""Read-only native viewport labels. This module runs on Max's main thread."""
from __future__ import unicode_literals

LIMIT = 500
try:
    text_type = unicode
except NameError:
    text_type = str
_INSTALLED = False
RESET_SOURCE = '''
global qz_vc_numbers_stop
if qz_vc_numbers_stop != undefined do qz_vc_numbers_stop()
'''

# Fixed source only. Channel, mask and ownership are passed as runtime values,
# never inserted into executable MAXScript. No geometry/map cache is retained.
SOURCE = r'''
global qz_vc_numbers_state, qz_vc_numbers_draw, qz_vc_numbers_stop
if qz_vc_numbers_draw != undefined do unregisterRedrawViewsCallback qz_vc_numbers_draw
callbacks.removeScripts id:#qz_vc_numbers
struct QzVertexColorNumbersState (
    enabled=false, owner="", channel=0, mask=#(true,true,true),
    lastPing=0, count=0, limited=false, error="", sampleMs=0
)
qz_vc_numbers_state = QzVertexColorNumbersState()

fn qz_vc_numbers_stop = (
    qz_vc_numbers_state.enabled = false
    qz_vc_numbers_state.owner = ""
    qz_vc_numbers_state.count = 0
    unregisterRedrawViewsCallback qz_vc_numbers_draw
    callbacks.removeScripts id:#qz_vc_numbers
    true
)

fn qz_vc_numbers_byte value = (
    if not (bit.isFinite value) do throw "Invalid vertex color"
    (floor ((amin 1.0 (amax 0.0 value)) * 255.0 + 0.5)) as integer
)

fn qz_vc_numbers_collect = (
    local state = qz_vc_numbers_state
    local rows = #()
    state.count = 0
    state.limited = false
    if not state.enabled or selection.count != 1 do return rows
    local obj = selection[1]
    if not isValidNode obj or classOf obj != Editable_Poly or obj.isHiddenInVpt do return rows
    if modPanel.getCurrentObject() != obj.baseObject do return rows
    if subObjectLevel < 1 or subObjectLevel > 5 do return rows
    if not (polyop.getMapSupport obj state.channel) do return rows
    local mapCount = polyop.getNumMapVerts obj state.channel
    if mapCount == 0 do return rows
    local verts = case subObjectLevel of (
        1: polyop.getVertSelection obj
        2: polyop.getVertsUsingEdge obj (polyop.getEdgeSelection obj)
        3: polyop.getVertsUsingEdge obj (polyop.getEdgeSelection obj)
        default: polyop.getVertsUsingFace obj (polyop.getFaceSelection obj)
    )
    state.limited = verts.numberSet > 500
    local dead = polyop.getDeadFaces obj
    local visited = 0
    local started = timeStamp()
    for vertex in verts while visited < 500 do (
        visited += 1
        local one = #{}
        one[vertex] = true
        local faces = (polyop.getFacesUsingVert obj one) - dead
        local chosen = 0
        local valid = true
        for face in faces while valid do (
            local geo = polyop.getFaceVerts obj face
            local mapping = polyop.getMapFace obj state.channel face
            if geo.count != mapping.count then valid = false
            else for corner = 1 to geo.count where geo[corner] == vertex do (
                local mid = mapping[corner]
                if mid < 1 or mid > mapCount then valid = false
                else if chosen == 0 or mid < chosen do chosen = mid
            )
        )
        if valid and chosen > 0 do (
            -- An invalid external color hides this label, without changing data.
            try (
                local colorValue = polyop.getMapVert obj state.channel chosen
                local values = #(qz_vc_numbers_byte colorValue.x,
                                 qz_vc_numbers_byte colorValue.y,
                                 qz_vc_numbers_byte colorValue.z)
                local world = (polyop.getVert obj.baseObject vertex) * obj.objectTransform
                if bit.isFinite world.x and bit.isFinite world.y and bit.isFinite world.z do
                    append rows #(vertex, world, values)
            ) catch ()
        )
    )
    state.sampleMs = timeStamp() - started
    rows
)

fn qz_vc_numbers_draw = (
    local state = qz_vc_numbers_state
    if not state.enabled do return()
    local age = timeStamp() - state.lastPing
    if age < 0 or age > 30000 do (qz_vc_numbers_stop(); return())
    try (
        local rows = qz_vc_numbers_collect()
        gw.setTransform (matrix3 1)
        local width = gw.getWinSizeX applyUIScaling:false
        local height = gw.getWinSizeY applyUIScaling:false
        local viewTM = viewport.getTM()
        local perspective = gw.isPerspectiveView()
        local cell = (gw.getTextExtent "255" applyUIScaling:false).x + 6
        local colors = #((color 255 80 80), (color 80 255 80), (color 90 160 255))
        local shown = 0
        for row in rows do (
            local world = row[2]
            if not perspective or (world * viewTM).z < 0 do (
                local screen = gw.wTransPoint world applyUIScaling:false
                if screen.x >= 0 and screen.y >= 0 and screen.x < width and screen.y < height do (
                    -- wText and wTransPoint both use device coordinates with Y down.
                    local x = screen.x + 3
                    local y = screen.y - 1
                    local used = 0
                    for component = 1 to 3 where state.mask[component] do (
                        local label = row[3][component] as string
                        -- One-pixel shadow keeps pure numeric labels legible.
                        gw.wText [x+1,y+1,0] label color:black applyUIScaling:false
                        gw.wText [x,y,0] label color:colors[component] applyUIScaling:false
                        x += cell
                        used += 1
                    )
                    if used > 0 do shown += 1
                )
            )
        )
        state.count = shown
        gw.enlargeUpdateRect #whole
    ) catch (
        state.error = getCurrentException()
        qz_vc_numbers_stop()
    )
)

fn qz_vc_numbers_configure enabled channel mask owner = (
    local state = qz_vc_numbers_state
    if not enabled then (
        if state.owner == owner do qz_vc_numbers_stop()
    ) else (
        state.channel = channel
        state.mask = mask
        state.owner = owner
        state.error = ""
        state.lastPing = timeStamp()
        state.enabled = true
        unregisterRedrawViewsCallback qz_vc_numbers_draw
        registerRedrawViewsCallback qz_vc_numbers_draw
        callbacks.removeScripts id:#qz_vc_numbers
        for event in #(#systemPreReset, #systemPreNew, #filePreOpen) do
            callbacks.addScript event "qz_vc_numbers_stop()" id:#qz_vc_numbers persistent:false
    )
    state.enabled
)

fn qz_vc_numbers_status owner = (
    local state = qz_vc_numbers_state
    if state.enabled and state.owner == owner do state.lastPing = timeStamp()
    #(state.enabled and state.owner == owner, state.channel, state.mask,
      state.count, state.limited, state.error, state.sampleMs)
)
'''


def reset(rt):
    global _INSTALLED
    rt.execute(RESET_SOURCE)
    _INSTALLED = False


def configure(rt, enabled, channel, mask, owner):
    global _INSTALLED
    if not _INSTALLED:
        rt.execute(SOURCE)
        _INSTALLED = True
    rt.qz_vc_numbers_configure(enabled, channel, rt.Array(*mask), owner)
    rt.redrawViews()
    return status(rt, owner)


def status(rt, owner):
    if not _INSTALLED:
        return dict(enabled=False, channel=0, mask=[True]*3, count=0,
                    limited=False, error='', limit=LIMIT)
    state = rt.qz_vc_numbers_status(owner)
    return dict(enabled=bool(state[0]), channel=int(state[1]), mask=list(state[2]),
                count=int(state[3]), limited=bool(state[4]), error=text_type(state[5]), limit=LIMIT)


# Redraw callbacks survive Python module reload and scene changes. Unregister
# the old native function before this action module can be enabled again.
try:
    import pymxs
except ImportError:
    pass
else:
    reset(pymxs.runtime)
