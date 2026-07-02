--[[
  bios.lua - the "operating system" half of the emulator.

  main.py registers a handful of native (Python-backed) functions before
  running this file: term.write/blit/clear/..., fs.open/exists/..., 
  os.startTimer/queueEvent/cancelTimer, peripheral.find/wrap/isPresent.

  Everything else - print(), read(), os.pullEvent(), colors/colours,
  keys, textutils, dofile()/os.run() and the "lua>" prompt - is plain
  Lua, exactly like real ComputerCraft's bios.lua sits on top of the
  native peripherals provided by the Java host.
]]

------------------------------------------------------------
-- colours / colors
------------------------------------------------------------
colours = {
  white = 1, orange = 2, magenta = 4, lightBlue = 8,
  yellow = 16, lime = 32, pink = 64, gray = 128, grey = 128,
  lightGray = 256, lightGrey = 256, cyan = 512, purple = 1024,
  blue = 2048, brown = 4096, green = 8192, red = 16384, black = 32768,
}

local BLIT_CHARS = "0123456789abcdef"
local COLOUR_ORDER = {1,2,4,8,16,32,64,128,256,512,1024,2048,4096,8192,16384,32768}
local blitLookup = {}
for i, v in ipairs(COLOUR_ORDER) do
  blitLookup[v] = BLIT_CHARS:sub(i, i)
end

function colours.toBlit(c)
  return blitLookup[c] or "0"
end

function colours.fromBlit(hex)
  local idx = BLIT_CHARS:find(hex, 1, true)
  if not idx then return colours.white end
  return COLOUR_ORDER[idx]
end

function colours.combine(...)
  local r = 0
  for _, c in ipairs({...}) do r = r | c end
  return r
end

function colours.subtract(c, ...)
  local r = c
  for _, v in ipairs({...}) do r = r & ~v end
  return r
end

function colours.test(c, mask)
  return (c & mask) == mask
end

function colours.packRGB(r, g, b)
  return ((math.floor(r * 255)) << 16) | ((math.floor(g * 255)) << 8) | math.floor(b * 255)
end

function colours.unpackRGB(rgb)
  return ((rgb >> 16) & 0xFF) / 255, ((rgb >> 8) & 0xFF) / 255, (rgb & 0xFF) / 255
end

colors = colours

------------------------------------------------------------
-- keys
------------------------------------------------------------
keys = {
  a=1,b=2,c=3,d=4,e=5,f=6,g=7,h=8,i=9,j=10,k=11,l=12,m=13,n=14,o=15,p=16,
  q=17,r=18,s=19,t=20,u=21,v=22,w=23,x=24,y=25,z=26,
  ["0"]=27,["1"]=28,["2"]=29,["3"]=30,["4"]=31,["5"]=32,["6"]=33,["7"]=34,["8"]=35,["9"]=36,
  space=37, enter=38, backspace=39, tab=40, escape=41,
  up=42, down=43, left=44, right=45,
  leftShift=46, rightShift=47, leftCtrl=48, rightCtrl=49, leftAlt=50, rightAlt=51,
  home=52, ["end"]=53, pageUp=54, pageDown=55, delete=56, insert=57,
  f1=58,f2=59,f3=60,f4=61,f5=62,f6=63,f7=64,f8=65,f9=66,f10=67,f11=68,f12=69,
  minus=70, equals=71, leftBracket=72, rightBracket=73, semicolon=74,
  apostrophe=75, comma=76, period=77, slash=78, backslash=79, grave=80,
  numPadEnter=38,
}
local keyNames = {}
for name, code in pairs(keys) do
  if keyNames[code] == nil then keyNames[code] = name end
end
function keys.getName(code)
  return keyNames[code]
end

------------------------------------------------------------
-- event queue / os.pullEvent (the coroutine-yield trick real CC uses)
------------------------------------------------------------
local eventQueue = {}

function os.queueEvent(...)
  table.insert(eventQueue, table.pack(...))
end

function os.pullEventRaw(filter)
  while true do
    local ev
    if #eventQueue > 0 then
      ev = table.remove(eventQueue, 1)
    else
      ev = table.pack(coroutine.yield(filter))
    end
    if filter == nil or ev[1] == filter then
      return table.unpack(ev, 1, ev.n)
    end
  end
end

function os.pullEvent(filter)
  local ev = table.pack(os.pullEventRaw(filter))
  if ev[1] == "terminate" then
    error("Terminated", 0)
  end
  return table.unpack(ev, 1, ev.n)
end

function os.sleep(t)
  local id = os.startTimer(t or 0)
  while true do
    local _, tid = os.pullEvent("timer")
    if tid == id then return end
  end
end

------------------------------------------------------------
-- print / write / read
------------------------------------------------------------
function write(text)
  term.write(tostring(text))
end

function print(...)
  local n = select("#", ...)
  local parts = {}
  for i = 1, n do
    parts[i] = tostring((select(i, ...)))
  end
  term.write(table.concat(parts, "\t"))
  term.write("\n")
end

function printError(...)
  local fg = term.getTextColour and term.getTextColour() or colours.white
  term.setTextColour(colours.red)
  print(...)
  term.setTextColour(fg)
end

function read(replaceChar, history)
  local input = ""
  local pos = 0
  local sx, sy = term.getCursorPos()

  local function redraw()
    local w = ({term.getSize()})[1]
    term.setCursorPos(sx, sy)
    local shown = replaceChar and string.rep(replaceChar, #input) or input
    term.write(shown .. string.rep(" ", math.max(0, w - sx - #shown + 1)))
    term.setCursorPos(sx + #input, sy)
  end

  redraw()
  while true do
    local event, p1 = os.pullEvent()
    if event == "char" then
      input = input .. p1
      redraw()
    elseif event == "key" then
      if p1 == keys.enter or p1 == keys.numPadEnter then
        term.setCursorPos(sx, sy)
        term.write(replaceChar and string.rep(replaceChar, #input) or input)
        term.write("\n")
        return input
      elseif p1 == keys.backspace then
        if #input > 0 then
          input = input:sub(1, #input - 1)
          redraw()
        end
      end
    elseif event == "paste" then
      input = input .. p1
      redraw()
    end
  end
end

------------------------------------------------------------
-- textutils
------------------------------------------------------------
textutils = {}

local function serializeImpl(v, indent, seen)
  local ty = type(v)
  if ty == "string" then
    return string.format("%q", v)
  elseif ty == "number" or ty == "boolean" then
    return tostring(v)
  elseif ty == "nil" then
    return "nil"
  elseif ty == "table" then
    if seen[v] then error("Cannot serialize a table with a recursive reference", 0) end
    seen[v] = true
    local lines = {"{"}
    local newIndent = indent .. "  "
    for k, val in pairs(v) do
      local key
      if type(k) == "string" and k:match("^[%a_][%w_]*$") then
        key = k .. " = "
      else
        key = "[" .. serializeImpl(k, newIndent, seen) .. "] = "
      end
      table.insert(lines, newIndent .. key .. serializeImpl(val, newIndent, seen) .. ",")
    end
    table.insert(lines, indent .. "}")
    seen[v] = nil
    return table.concat(lines, "\n")
  else
    error("Cannot serialize type " .. ty, 0)
  end
end

function textutils.serialize(t)
  return serializeImpl(t, "", {})
end
textutils.serialise = textutils.serialize

function textutils.unserialize(str)
  local f = load("return " .. str, "=unserialize", "t", {})
  if not f then return nil end
  local ok, result = pcall(f)
  if not ok then return nil end
  return result
end
textutils.unserialise = textutils.unserialize

function textutils.urlEncode(str)
  return (str:gsub("[^%w%-%_%.%~]", function(c)
    return string.format("%%%02X", string.byte(c))
  end))
end

function textutils.formatTime(t, twentyFourHour)
  local hour = math.floor(t) % 24
  local minute = math.floor((t - math.floor(t)) * 60)
  if twentyFourHour then
    return string.format("%d:%02d", hour, minute)
  end
  local suffix = hour >= 12 and "PM" or "AM"
  local h12 = hour % 12
  if h12 == 0 then h12 = 12 end
  return string.format("%d:%02d %s", h12, minute, suffix)
end

------------------------------------------------------------
-- virtual filesystem helpers: dofile / os.run
------------------------------------------------------------
function dofile(path)
  local f, err = fs.open(path, "r")
  if not f then error((err or ("File not found: " .. path)), 2) end
  local content = f.readAll()
  f.close()
  local fn, loadErr = load(content, "@" .. path, "t", _G)
  if not fn then error(loadErr, 2) end
  return fn()
end

function os.run(env, path, ...)
  local f, err = fs.open(path, "r")
  if not f then
    printError(err or ("File not found: " .. path))
    return false
  end
  local content = f.readAll()
  f.close()
  local runEnv = setmetatable(env or {}, { __index = _G })
  local fn, loadErr = load(content, "@" .. path, "t", runEnv)
  if not fn then
    printError(loadErr)
    return false
  end
  local ok, runErr = pcall(fn, ...)
  if not ok then
    printError(runErr)
    return false
  end
  return true
end

function run(path, ...)
  return os.run({}, path, ...)
end

function ls(path)
  path = path or "/"
  local items = fs.list(path)
  if not items then
    printError("No such directory: " .. path)
    return
  end
  table.sort(items)
  for _, name in ipairs(items) do
    if fs.isDir(fs.combine(path, name)) then
      term.setTextColour(colours.cyan)
      print(name .. "/")
      term.setTextColour(colours.white)
    else
      print(name)
    end
  end
end

------------------------------------------------------------
-- Boot sequence
------------------------------------------------------------
local ok, bootErr = pcall(function()
  term.setBackgroundColour(colours.black)
  term.setTextColour(colours.white)
  term.clear()
  term.setCursorPos(1, 1)
  print("CraftOS-Py 1.0")
  print("Emulated ComputerCraft-style environment")
  print("Type Lua directly at the prompt. 'ls()' lists files,")
  print("'run(\"name.lua\")' runs a program, 'exit' quits the prompt.")
  print("")

  if fs.exists("/startup.lua") then
    local sok, serr = pcall(dofile, "/startup.lua")
    if not sok then
      printError("startup.lua error: " .. tostring(serr))
    end
  end

  while true do
    term.setTextColour(colours.yellow)
    term.write("lua> ")
    term.setTextColour(colours.white)
    local line = read()

    if line == "exit" then
      break
    elseif line ~= "" then
      local fn, err = load("return " .. line, "=lua", "t", _G)
      if not fn then
        fn, err = load(line, "=lua", "t", _G)
      end
      if fn then
        local results = table.pack(pcall(fn))
        if not results[1] then
          printError(tostring(results[2]))
        else
          for i = 2, results.n do
            term.setTextColour(colours.lightGray)
            print(tostring(results[i]))
          end
          term.setTextColour(colours.white)
        end
      else
        printError(tostring(err))
      end
    end
  end

  term.setTextColour(colours.orange)
  print("Prompt exited. The computer is now idle (Reboot to restart).")
  term.setTextColour(colours.white)
  while true do
    os.pullEvent()
  end
end)

if not ok then
  term.setTextColour(colours.red)
  print("BIOS ERROR: " .. tostring(bootErr))
  term.setTextColour(colours.white)
  while true do
    os.pullEvent()
  end
end
